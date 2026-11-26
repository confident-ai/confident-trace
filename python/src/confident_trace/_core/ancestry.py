"""Destination-local ancestry selection over public OTel span callbacks."""

import logging
import sys
from collections import OrderedDict
from dataclasses import dataclass

from .._attributes import SCOPE_NAME

log = logging.getLogger(SCOPE_NAME)
MAX_PENDING_SPANS = 1024
MAX_PENDING_BYTES = 16 * 1024 * 1024


def span_key(context):
    return context.trace_id, context.span_id


def estimated_size(span):
    """Conservative payload estimate, not a process RSS limit.

    Include container overhead and double worst-case UTF-8 string storage. Count shared
    values repeatedly, including resource/scope data, rather than undercounting.
    """

    def size(value):
        if isinstance(value, str):
            return sys.getsizeof(value) + 8 * len(value)
        if hasattr(value, "items"):
            return sys.getsizeof(value) + sum(
                size(k) + size(v) + 64 for k, v in value.items()
            )
        if isinstance(value, (tuple, list)):
            return sys.getsizeof(value) + sum(size(v) + 16 for v in value)
        return sys.getsizeof(value)

    result = 2048 + size(span.name) + size(span.attributes)
    result += size(getattr(span.resource, "attributes", {}))
    scope = span.instrumentation_scope
    if scope:
        result += size(scope.name) + size(scope.version) + size(scope.attributes)
    for event in span.events:
        result += 256 + size(event.name) + size(event.attributes)
    for link in span.links:
        result += 256 + size(link.attributes)
    result += size(span.status.description)
    return result


@dataclass
class Node:
    route: object
    parent: object = None
    children: int = 0
    ended: bool = False
    selected: bool = False
    payload: object = None
    size: int = 0


class Ancestry:
    """Caller serializes access; only ended unresolved payloads are buffered."""

    def __init__(self, publish):
        self.nodes = {}
        self.pending = OrderedDict()
        self.pending_bytes = 0
        self.publish = publish

    def start(self, key, parent, route, selected):
        ancestor = self.nodes.get(parent)
        if ancestor is None or ancestor.route is not route:
            parent = None
        else:
            ancestor.children += 1
        self.nodes[key] = Node(route, parent)
        if selected:
            self.select(key)

    def select(self, key):
        while key in self.nodes:
            node = self.nodes[key]
            if node.selected:
                break
            node.selected = True
            if node.payload is not None:
                payload = node.payload
                self._release_payload(key, node)
                self.publish(node.route, payload)
            key = node.parent

    def _release_payload(self, key, node):
        self.pending.pop(key, None)
        self.pending_bytes -= node.size
        node.payload, node.size = None, 0

    def end(self, key, span, selected):
        node = self.nodes.get(key)
        if node is None:
            return
        node.ended = True
        if selected:
            self.select(key)
        if node.selected:
            self.publish(node.route, span)
        elif node.children:
            node.payload, node.size = span, estimated_size(span)
            self.pending[key] = None
            self.pending_bytes += node.size
        self._clean(key)
        if (
            len(self.pending) > MAX_PENDING_SPANS
            or self.pending_bytes > MAX_PENDING_BYTES
        ):
            log.debug(
                "Ancestry buffer limit reached; dropping oldest pending ancestors"
            )
        while (
            len(self.pending) > MAX_PENDING_SPANS
            or self.pending_bytes > MAX_PENDING_BYTES
        ):
            self._drop(next(iter(self.pending)))

    def _clean(self, key):
        while key in self.nodes:
            node = self.nodes[key]
            if not node.ended or node.children:
                break
            self._release_payload(key, node)
            del self.nodes[key]
            node.route.active -= 1
            key = node.parent
            if key in self.nodes:
                self.nodes[key].children -= 1

    def _drop(self, key):
        """Discard a buffered payload that has no AI descendant yet.

        The node stays tracked so its subtree's bookkeeping holds; if a descendant
        qualifies later, selection passes through it to the ancestors still held.
        """
        self._release_payload(key, self.nodes[key])

    def clear(self):
        for node in self.nodes.values():
            node.route.active -= 1
        self.nodes.clear()
        self.pending.clear()
        self.pending_bytes = 0
