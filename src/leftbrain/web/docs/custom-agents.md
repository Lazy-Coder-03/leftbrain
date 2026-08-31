# Custom agents

You do not need an MCP client library, a framework, or even a dependency to use leftbrain. The
endpoint is one HTTP POST away in any language. This page goes from the raw protocol up: the
JSON-RPC calls, an MCP client in eight languages, the framework wiring, and a plain-HTTP fallback
for everything else.

<div class="callout">Every tool answers with the same contract: <code>{ok: true, result, assumptions[], warnings[]}</code>, or <code>{ok: false, error, message, retryable}</code> with an optional <code>needs</code> block when the input was ambiguous. <code>retryable</code> is <code>false</code> for almost every failure — only <code>busy</code> invites a retry — so an agent knows when repeating the call is pointless. Every response also carries a <code>meta</code> block: <code>latency_ms</code>, <code>compute_ms</code>, <code>truncated</code>, and <code>quota</code> so you can back off before a 429. That object is the <code>structuredContent</code> of the MCP result, and the same JSON is repeated as text in <code>content[0].text</code> for clients that only read text.</div>

Every snippet below is labelled. **Executed** ones were run, unchanged apart from the URL and the
key, against a running leftbrain server while this page was written. **From the SDK docs** ones were
written against that SDK's current documentation and were not run here.

## The protocol in three sentences

MCP is JSON-RPC 2.0 over one HTTP endpoint: you `POST` a request object and read a response object.
Two methods do everything — `tools/list` returns the tool names and their JSON Schemas, and
`tools/call` runs one of them with an arguments object. leftbrain's endpoint is **stateless**, so
there is no session to open and no `initialize` handshake required before you call a tool; the
`Mcp-Session-Id` header a stateful server would hand back is simply absent.

:::request
```http
POST /mcp HTTP/1.1
Host: leftbrain.idlesync.in
Authorization: Bearer lblz_YOUR_KEY
Content-Type: application/json
Accept: application/json, text/event-stream
```
:::

The [quickstart](/docs/quickstart#call-a-tool-over-mcp) has the copy-paste `curl` for all three
operating systems. The two request bodies are:

:::request tools/list
```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
```
:::

:::request tools/call
```json
{"jsonrpc": "2.0", "id": 2, "method": "tools/call",
 "params": {"name": "numbers", "arguments": {"mode": "compare", "values": ["9.11", "9.9"]}}}
```
:::

and a successful `tools/call` answers with a JSON-RPC envelope whose `result` carries three fields:

:::response
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [{"type": "text", "text": "{ … the same JSON, as text … }"}],
    "structuredContent": {
      "ok": true,
      "result": {"max": {"input": "9.9", "value": "9.9"}, "ordering": "9.11 < 9.9", "…": "…"},
      "assumptions": [],
      "warnings": []
    },
    "isError": false
  }
}
```
:::

Read `structuredContent`. `isError: true` means the call never reached the tool — a missing or
mistyped argument, or an unknown tool name — and then `content[0].text` carries the reason. A tool
that ran and refused answers with `isError: false` and `structuredContent.ok: false`: that is a
result, not a transport failure.

## An MCP client, per language

Each client does the same three things: connect with the bearer header, list the tools, and call
`numbers` in `compare` mode. `9.9` is the larger number; a model that says otherwise is the reason
this server exists.

### Python — `mcp`

**Executed.** `pip install mcp httpx`

:::request
```python
import asyncio
import os

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = "https://leftbrain.idlesync.in/mcp"
KEY = os.environ["LB_KEY"]


async def main() -> None:
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {KEY}"}) as http:
        async with streamable_http_client(URL, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                print("tools:", [t.name for t in tools.tools])

                result = await session.call_tool(
                    "numbers", {"mode": "compare", "values": ["9.11", "9.9"]}
                )
                print("max:", result.structured_content["result"]["max"])


asyncio.run(main())
```
:::

:::response
```text
tools: ['math', 'datetime', 'scale', 'convert', 'numbers', 'finance', 'text', 'collections', 'validate', 'random', 'geo_offline', 'encode', 'color']
max: {'input': '9.9', 'value': '9.9'}
```
:::

Each tool's JSON Schema is on `t.input_schema`, which is what you convert when you hand the tools to
a model.

### TypeScript / JavaScript — `@modelcontextprotocol/sdk`

**Executed** with Node 22. `npm i @modelcontextprotocol/sdk`

:::request
```ts
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const url = "https://leftbrain.idlesync.in/mcp";
const key = process.env.LB_KEY!;

const transport = new StreamableHTTPClientTransport(new URL(url), {
  requestInit: { headers: { Authorization: `Bearer ${key}` } },
});

const client = new Client({ name: "my-agent", version: "1.0.0" });
await client.connect(transport);

const { tools } = await client.listTools();
console.log("tools:", tools.map((t) => t.name).join(", "));

const res = await client.callTool({
  name: "numbers",
  arguments: { mode: "compare", values: ["9.11", "9.9"] },
});
console.log("max:", res.structuredContent.result.max);

await client.close();
```
:::

:::response
```text
tools: math, datetime, scale, convert, numbers, finance, text, collections, validate, random, geo_offline, encode, color
max: { input: '9.9', value: '9.9' }
```
:::

The schema is `t.inputSchema` here — camelCase, unlike the Python SDK.

### Go — `github.com/modelcontextprotocol/go-sdk`

**From the SDK docs.** `go get github.com/modelcontextprotocol/go-sdk/mcp`

`StreamableClientTransport` is a plain struct, so headers go on the `*http.Client` you hand it.

:::request
```go
package main

import (
	"context"
	"fmt"
	"net/http"
	"os"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type bearer struct{ key string }

func (b bearer) RoundTrip(r *http.Request) (*http.Response, error) {
	r.Header.Set("Authorization", "Bearer "+b.key)
	return http.DefaultTransport.RoundTrip(r)
}

func main() {
	ctx := context.Background()
	transport := &mcp.StreamableClientTransport{
		Endpoint:   "https://leftbrain.idlesync.in/mcp",
		HTTPClient: &http.Client{Transport: bearer{os.Getenv("LB_KEY")}},
	}

	client := mcp.NewClient(&mcp.Implementation{Name: "my-agent", Version: "1.0.0"}, nil)
	session, err := client.Connect(ctx, transport, nil)
	if err != nil {
		panic(err)
	}
	defer session.Close()

	tools, _ := session.ListTools(ctx, nil)
	for _, t := range tools.Tools {
		fmt.Println(t.Name)
	}

	res, err := session.CallTool(ctx, &mcp.CallToolParams{
		Name:      "numbers",
		Arguments: map[string]any{"mode": "compare", "values": []string{"9.11", "9.9"}},
	})
	if err != nil {
		panic(err)
	}
	fmt.Println(res.StructuredContent)
}
```
:::

### Rust — `rmcp`

**From the SDK docs.**

```toml
[dependencies]
rmcp = { version = "3", features = ["client", "transport-streamable-http-client-reqwest"] }
tokio = { version = "1", features = ["full"] }
```

:::request
```rust
use rmcp::model::CallToolRequestParams;
use rmcp::transport::{StreamableHttpClientTransport, StreamableHttpClientTransportConfig};
use rmcp::ClientInfo;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config = StreamableHttpClientTransportConfig::with_uri("https://leftbrain.idlesync.in/mcp")
        .auth_header(std::env::var("LB_KEY")?); // the token only — rmcp adds "Bearer "
    let transport = StreamableHttpClientTransport::with_client(reqwest::Client::new(), config);

    let client = ClientInfo::default().serve(transport).await?;
    for tool in client.list_all_tools().await? {
        println!("{}", tool.name);
    }

    let result = client
        .call_tool(
            CallToolRequestParams::new("numbers")
                .with_arguments(serde_json::json!({"mode": "compare", "values": ["9.11", "9.9"]})),
        )
        .await?;
    println!("{result:?}");
    Ok(())
}
```
:::

### Java — MCP Java SDK

**From the SDK docs.** `io.modelcontextprotocol.sdk:mcp-core`

:::request
```java
McpTransport transport = HttpClientStreamableHttpTransport
        .builder("https://leftbrain.idlesync.in")
        .endpoint("/mcp")
        .customizeRequest(b -> b.header("Authorization", "Bearer " + System.getenv("LB_KEY")))
        .build();

McpSyncClient client = McpClient.sync(transport).build();
client.initialize();

client.listTools().tools().forEach(t -> System.out.println(t.name()));

CallToolResult result = client.callTool(
        CallToolRequest.builder("numbers")
                .arguments(Map.of("mode", "compare", "values", List.of("9.11", "9.9")))
                .build());
System.out.println(result.structuredContent());
```
:::

### C# — `ModelContextProtocol`

**From the SDK docs.** `dotnet add package ModelContextProtocol`

:::request
```csharp
using ModelContextProtocol.Client;

var transport = new HttpClientTransport(new HttpClientTransportOptions
{
    Name = "leftbrain",
    Endpoint = new Uri("https://leftbrain.idlesync.in/mcp"),
    TransportMode = HttpTransportMode.StreamableHttp,
    AdditionalHeaders = new Dictionary<string, string>
    {
        ["Authorization"] = $"Bearer {Environment.GetEnvironmentVariable("LB_KEY")}"
    }
});

await using var client = await McpClient.CreateAsync(transport);

foreach (var tool in await client.ListToolsAsync())
    Console.WriteLine(tool.Name);

var result = await client.CallToolAsync("numbers", new Dictionary<string, object?>
{
    ["mode"] = "compare",
    ["values"] = new[] { "9.11", "9.9" }
});
```
:::

### Swift — `modelcontextprotocol/swift-sdk`

**From the SDK docs.** `.package(url: "https://github.com/modelcontextprotocol/swift-sdk.git", from: "0.11.0")`

`HTTPClientTransport` takes a `requestModifier`, which is where the header goes.

:::request
```swift
import MCP

let key = ProcessInfo.processInfo.environment["LB_KEY"]!
let transport = HTTPClientTransport(
    endpoint: URL(string: "https://leftbrain.idlesync.in/mcp")!,
    streaming: true,
    requestModifier: { request in
        var request = request
        request.addValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        return request
    }
)

let client = Client(name: "my-agent", version: "1.0.0")
try await client.connect(transport: transport)

let (tools, _) = try await client.listTools()
print(tools.map { $0.name })

let (content, isError) = try await client.callTool(
    name: "numbers",
    arguments: ["mode": "compare", "values": ["9.11", "9.9"]]
)
```
:::

### Kotlin — `io.modelcontextprotocol:kotlin-sdk-client`

**From the SDK docs.** The header goes on the Ktor client you pass to the transport.

:::request
```kotlin
val http = HttpClient {
    install(SSE)
    defaultRequest { headers.append(HttpHeaders.Authorization, "Bearer ${System.getenv("LB_KEY")}") }
}

val client = Client(clientInfo = Implementation(name = "my-agent", version = "1.0.0"))
client.connect(StreamableHttpClientTransport(client = http, url = "https://leftbrain.idlesync.in/mcp"))

println(client.listTools().tools.map { it.name })

val result = client.callTool(
    name = "numbers",
    arguments = mapOf("mode" to "compare", "values" to listOf("9.11", "9.9")),
)
println(result)
```
:::

## Wiring it into a framework

### Anthropic Messages API — the tool-use loop

**From the SDK docs.** There is no MCP-specific step: `tools/list` schemas map one-to-one onto the
`tools` parameter, and every `tool_use` block becomes a `tools/call`.

Python (`pip install anthropic mcp httpx`):

:::request
```python
import anthropic

client = anthropic.Anthropic()

# `session` is the ClientSession from the Python example above.
listed = await session.list_tools()
tools = [
    {"name": t.name, "description": t.description, "input_schema": t.input_schema}
    for t in listed.tools
]

messages = [{"role": "user", "content": "Is 9.11 bigger than 9.9? Use the tools."}]

while True:
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        tools=tools,
        messages=messages,
    )
    messages.append({"role": "assistant", "content": response.content})
    if response.stop_reason != "tool_use":
        break

    results = []
    for block in response.content:
        if block.type != "tool_use":
            continue
        out = await session.call_tool(block.name, block.input)
        results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": out.content[0].text,          # the contract, as text
            "is_error": bool(out.is_error),
        })
    messages.append({"role": "user", "content": results})   # every result, one message

print(next(b.text for b in response.content if b.type == "text"))
```
:::

Two rules the loop depends on: return **all** the `tool_result` blocks of a turn in a *single* user
message, and append `response.content` unchanged rather than just its text.

TypeScript (`npm i @anthropic-ai/sdk @modelcontextprotocol/sdk`):

:::request
```ts
import Anthropic from "@anthropic-ai/sdk";

const anthropic = new Anthropic();
const { tools: listed } = await client.listTools();     // the Client from the TS example above
const tools = listed.map((t) => ({
  name: t.name,
  description: t.description ?? "",
  input_schema: t.inputSchema,
}));

const messages: Anthropic.MessageParam[] = [
  { role: "user", content: "Is 9.11 bigger than 9.9? Use the tools." },
];

for (;;) {
  const response = await anthropic.messages.create({
    model: "claude-opus-5",
    max_tokens: 16000,
    thinking: { type: "adaptive" },
    tools,
    messages,
  });
  messages.push({ role: "assistant", content: response.content });
  if (response.stop_reason !== "tool_use") break;

  const results: Anthropic.ToolResultBlockParam[] = [];
  for (const block of response.content) {
    if (block.type !== "tool_use") continue;
    const out = await client.callTool({ name: block.name, arguments: block.input as never });
    results.push({
      type: "tool_result",
      tool_use_id: block.id,
      content: JSON.stringify(out.structuredContent),
      is_error: Boolean(out.isError),
    });
  }
  messages.push({ role: "user", content: results });
}
```
:::

### OpenAI Agents SDK

**From the SDK docs.** `pip install openai-agents`

:::request
```python
from agents import Agent
from agents.mcp import MCPServerStreamableHttp

async with MCPServerStreamableHttp(
    name="leftbrain",
    params={
        "url": "https://leftbrain.idlesync.in/mcp",
        "headers": {"Authorization": f"Bearer {os.environ['LB_KEY']}"},
        "timeout": 10,
    },
    cache_tools_list=True,
) as server:
    agent = Agent(
        name="Assistant",
        instructions="Call the leftbrain tools before stating any number or date.",
        mcp_servers=[server],
    )
```
:::

### Vercel AI SDK

**From the SDK docs.** `npm i ai @ai-sdk/mcp @modelcontextprotocol/sdk`

:::request
```ts
import { createMCPClient } from "@ai-sdk/mcp";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { generateText } from "ai";

const mcp = await createMCPClient({
  transport: new StreamableHTTPClientTransport(new URL("https://leftbrain.idlesync.in/mcp"), {
    requestInit: { headers: { Authorization: `Bearer ${process.env.LB_KEY}` } },
  }),
});

try {
  const { text } = await generateText({
    model: "anthropic/claude-opus-5",
    tools: await mcp.tools(),
    prompt: "Is 9.11 bigger than 9.9?",
  });
  console.log(text);
} finally {
  await mcp.close();
}
```
:::

Older versions of the SDK export the same function from `ai` as `experimental_createMCPClient`.

### LangChain / LangGraph

**From the SDK docs.** `pip install langchain-mcp-adapters`

:::request
```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "leftbrain": {
        "transport": "streamable_http",
        "url": "https://leftbrain.idlesync.in/mcp",
        "headers": {"Authorization": f"Bearer {os.environ['LB_KEY']}"},
    }
})
tools = await client.get_tools()   # LangChain tools, ready for any agent
```
:::

### CrewAI, AutoGen, Semantic Kernel

**From the SDK docs.** CrewAI's `MCPServerAdapter` takes the endpoint and transport and yields
CrewAI tools:

:::request
```python
from crewai_tools import MCPServerAdapter

server_params = {"url": "https://leftbrain.idlesync.in/mcp", "transport": "streamable-http"}
with MCPServerAdapter(server_params) as tools:
    agent = Agent(role="Analyst", goal="Be exact", tools=tools)
```
:::

AutoGen reaches the same endpoint through `McpWorkbench` with `StreamableHttpServerParams`. CrewAI's
published examples do not show the header field, and Semantic Kernel's MCP plugin surface we could
not confirm — for those two, check your framework's own docs for where the `Authorization` header
goes, or put leftbrain behind a local proxy that adds it.

## Plain HTTP, no SDK

Any language with an HTTP client can talk to leftbrain directly. Four things to get right:

**The headers.** All three are required:

:::request
```text
Authorization: Bearer lblz_YOUR_KEY
Content-Type: application/json
Accept: application/json, text/event-stream
```
:::

If you send an `Accept` header at all it must name **both** types: `Accept: application/json`
alone comes back as `406 Not Acceptable` — `{"error": {"code": -32600, "message": "Not
Acceptable: Client must accept both application/json and text/event-stream"}}` — before the
request reaches any tool.

**The response is SSE by default.** A successful call comes back as `content-type:
text/event-stream` with one event, and the JSON-RPC object is the `data:` line:

:::response
```text
event: message
data: {"jsonrpc":"2.0","id":2,"result":{"content":[…],"structuredContent":{"ok":true,…},"isError":false}}
```
:::

So: read the body, take the first line beginning with `data: `, strip those six characters, and
parse the rest as JSON. There is no chunk to reassemble and no `[DONE]` sentinel — a stateless
`tools/call` sends exactly one event. In Python that is

:::request
```python
payload = next(json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: "))
```
:::

A self-hosted server started with `--json` answers with `content-type: application/json` and no SSE
framing at all; if you own the deployment and never want to parse events, that is the switch.

**Rate limits.** Every authenticated response carries the budget:

| header | meaning |
| --- | --- |
| `x-ratelimit-remaining-today` | calls left on this key before 00:00 UTC |
| `x-ratelimit-limit-day` | the key's daily quota |
| `x-ratelimit-limit-minute` | the key's per-minute ceiling |

**401 and 429 never reach the tool**, so they are plain JSON, not JSON-RPC:

:::response
```json
{"ok": false, "error": "missing key", "message": "send Authorization: Bearer <key>"}
```
:::

A `401` means the header is absent or the key is not recognised — re-reading the key and retrying is
pointless. A `429` carries `retry-after` in seconds and an `error` naming which limit you hit: a
per-minute one (`rate limit: 60 requests/minute`) clears within the minute, a daily one
(`daily quota of {{daily_quota_raw}} exhausted; resets at 00:00 UTC`) does not. Sleep for `retry-after` and retry;
do not retry a `401`.

## Next

- [Tools](/docs/tools) — every tool, every mode, with worked calls and the inputs that fail.
- [MCP clients](/docs/clients) — the same endpoint from an editor instead of your own code.
