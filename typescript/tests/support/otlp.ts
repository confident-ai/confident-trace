import { createServer } from 'node:http';
import { gunzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { Server, ServerCredentials } from '@grpc/grpc-js';
import type {
  ServiceDefinition,
  UntypedServiceImplementation,
  ServerUnaryCall,
  sendUnaryData,
} from '@grpc/grpc-js';
import { loadSync } from '@grpc/proto-loader';
import protobuf from 'protobufjs';

const protoRoot = fileURLToPath(new URL('./proto/', import.meta.url));
const service = 'opentelemetry/proto/collector/trace/v1/trace_service.proto';
const root = new protobuf.Root();
root.resolvePath = (_origin, target) => join(protoRoot, target);
root.loadSync(service);
const requestType = root.lookupType(
  'opentelemetry.proto.collector.trace.v1.ExportTraceServiceRequest',
);
export interface WireValue {
  stringValue?: string;
  intValue?: string;
  boolValue?: boolean;
  doubleValue?: number;
  arrayValue?: { values: WireValue[] };
}
export interface WireAttribute {
  key: string;
  value: WireValue;
}
export interface WireSpan {
  name: string;
  attributes?: WireAttribute[];
  events?: { name: string; attributes?: WireAttribute[] }[];
}
export interface WireRequest {
  resourceSpans: {
    resource?: { attributes?: WireAttribute[] };
    scopeSpans: {
      schemaUrl?: string;
      scope?: { name: string; version: string };
      spans: WireSpan[];
    }[];
  }[];
}
export function attributes(
  items: WireAttribute[] = [],
): Record<string, unknown> {
  const value = (item: WireValue): unknown =>
    item.stringValue ??
    item.boolValue ??
    (item.intValue !== undefined ? Number(item.intValue) : undefined) ??
    item.doubleValue ??
    item.arrayValue?.values.map(value);
  return Object.fromEntries(items.map((item) => [item.key, value(item.value)]));
}
export async function httpReceiver() {
  const received: {
    body: WireRequest;
    headers: Record<string, unknown>;
    url: string | undefined;
  }[] = [];
  const server = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.from(chunk));
    let data = Buffer.concat(chunks);
    if (request.headers['content-encoding'] === 'gzip') data = gunzipSync(data);
    received.push({
      body: requestType.toObject(requestType.decode(data), {
        longs: String,
      }) as WireRequest,
      headers: request.headers,
      url: request.url,
    });
    response.writeHead(200, { 'content-type': 'application/x-protobuf' });
    response.end();
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  if (!address || typeof address === 'string')
    throw new Error('Missing receiver address');
  return {
    received,
    url: `http://127.0.0.1:${address.port}`,
    close: () =>
      new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      ),
  };
}
export async function grpcReceiver() {
  const definition = loadSync(join(protoRoot, service), {
    includeDirs: [protoRoot],
    longs: String,
  });
  const server = new Server();
  const received: { body: WireRequest; headers: Record<string, unknown> }[] =
    [];
  const implementation: UntypedServiceImplementation = {
    export(
      call: ServerUnaryCall<WireRequest, object>,
      callback: sendUnaryData<object>,
    ) {
      received.push({
        body: call.request as WireRequest,
        headers: call.metadata.getMap(),
      });
      callback(null, {});
    },
  };
  server.addService(
    definition[
      'opentelemetry.proto.collector.trace.v1.TraceService'
    ] as ServiceDefinition,
    implementation,
  );
  const port = await new Promise<number>((resolve, reject) =>
    server.bindAsync(
      '127.0.0.1:0',
      ServerCredentials.createInsecure(),
      (error, port) => (error ? reject(error) : resolve(port)),
    ),
  );
  return {
    received,
    url: `http://127.0.0.1:${port}`,
    close: () =>
      new Promise<void>((resolve, reject) =>
        server.tryShutdown((error) => (error ? reject(error) : resolve())),
      ),
  };
}
