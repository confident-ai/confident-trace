"""Install owned patches; preserve pre-existing wrappers and restore only ours."""

import importlib

import wrapt

from ..._core import runtime


def install_targets(targets, wrapper_factory):
    undo = []
    for module, class_name, method in targets:
        try:
            loaded = importlib.import_module(module)
            cls = getattr(loaded, class_name) if class_name else loaded
            original = getattr(cls, method)
            if isinstance(
                original,
                (wrapt.ObjectProxy, wrapt.FunctionWrapper, wrapt.BoundFunctionWrapper),
            ):
                continue
            patched = wrapt.FunctionWrapper(original, wrapper_factory(original, method))
            setattr(cls, method, patched)

            def restore(cls=cls, method=method, original=original, patched=patched):
                if vars(cls).get(method) is patched:
                    setattr(cls, method, original)

            undo.append(restore)
        except (ImportError, AttributeError):
            continue
        except Exception:
            runtime.log.debug("Instrumentation target unavailable")
    return undo
