"""Stable check ownership; runtime/dependency variants never become CI jobs."""

PYTHON_VERSIONS = ("3.10", "3.11", "3.12", "3.13")
NODE_VERSIONS = ("22", "24")
PROFILES = (
    "base",
    "native",
    "microsoft",
    "agent-sdks",
    "langchain",
    "crewai",
    "frameworks",
)
PYTHON = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google-genai": "Google GenAI",
    "bedrock": "Bedrock",
    "google-adk": "Google ADK",
    "agentcore": "AgentCore",
    "strands": "Strands",
    "pydantic-ai": "Pydantic AI",
    "microsoft": "Microsoft Agent Framework",
    "openai-agents": "OpenAI Agents",
    "claude-agent": "Claude Agent SDK",
    "langchain": "LangChain / LangGraph",
    "crewai": "CrewAI",
    "llamaindex": "LlamaIndex",
    "agno": "Agno",
    "smolagents": "smolagents",
    "core": "Core",
    "quality": "Build & quality",
    "interop": "Interoperability",
}
TYPESCRIPT = {
    key: PYTHON[key]
    for key in (
        "openai",
        "anthropic",
        "google-genai",
        "langchain",
        "openai-agents",
        "core",
        "quality",
    )
} | {"mastra": "Mastra", "vercel-ai": "Vercel AI SDK"}
HOME = {
    "google-adk": "native",
    "agentcore": "native",
    "strands": "native",
    "pydantic-ai": "native",
    "microsoft": "microsoft",
    "openai-agents": "agent-sdks",
    "claude-agent": "agent-sdks",
    "langchain": "langchain",
    "crewai": "crewai",
    "llamaindex": "frameworks",
    "agno": "frameworks",
    "smolagents": "frameworks",
}
REQUIRED = {
    "google-adk": ["google-adk"],
    "agentcore": [
        "bedrock-agentcore",
        "opentelemetry-instrumentation-asgi",
        "strands-agents",
    ],
    "strands": ["strands-agents"],
    "pydantic-ai": ["pydantic-ai-slim"],
    "microsoft": ["agent-framework-core", "agent-framework-openai"],
    "openai-agents": ["openai-agents", "openinference-instrumentation-openai-agents"],
    "claude-agent": ["claude-agent-sdk"],
    "langchain": ["langchain", "langgraph", "langchain-openai"],
    "crewai": ["crewai"],
    "llamaindex": ["llama-index-core", "llama-index-llms-openai"],
    "agno": ["agno"],
    "smolagents": ["smolagents"],
}
MINIMUM = [
    "opentelemetry-api==1.39.0",
    "opentelemetry-sdk==1.39.0",
    "opentelemetry-exporter-otlp-proto-http==1.39.0",
    "opentelemetry-exporter-otlp-proto-grpc==1.39.0",
    "openai==1.109.0",
    "anthropic==0.69.0",
    "google-genai==1.40.0",
    "wrapt==1.17.3",
    "boto3==1.40.0",
    "botocore==1.40.0",
]


def combinations(language, suite):
    if language == "typescript":
        return [(v, "base", d) for v in NODE_VERSIONS for d in ("minimum", "current")]
    profiles = (
        ("base",)
        if suite == "quality"
        else (HOME[suite],)
        if suite in HOME
        else PROFILES
    )
    rows = [
        (v, p, d)
        for v in PYTHON_VERSIONS
        for p in profiles
        for d in (("minimum", "latest") if p == "base" else ("tested", "latest"))
    ]
    if suite == "interop":
        rows.append(("3.13", "mega", "current"))
    return rows


def python_owner(nodeid, profile):
    """Partition the *original full collection* in each original environment.

    Integrations installed transitively outside their home environment remain
    covered by Interoperability. No dependency-graph assumptions lose coverage.
    """
    path = nodeid.split("::")[0].replace("\\", "/")
    path = path.split("python/tests/")[-1]
    if path.startswith("tests/"):
        path = path[len("tests/") :]
    if path.startswith(("core/", "contracts/")):
        return "core"
    direct = {
        "openai": "openai",
        "anthropic": "anthropic",
        "google_genai": "google-genai",
        "bedrock": "bedrock",
        "google_adk": "google-adk",
        "agentcore": "agentcore",
        "microsoft": "microsoft",
        "langchain": "langchain",
        "crewai": "crewai",
    }
    parts = path.split("/")
    if parts[0] != "integrations":
        raise ValueError(f"Unassigned Python test: {nodeid}")
    family = parts[1]
    if family in direct:
        owner = direct[family]
    elif family == "test_native_lifecycle.py":
        return "interop"
    elif family == "native_agents":
        owner = (
            "pydantic-ai"
            if "[pydantic_ai-" in nodeid
            else "strands"
            if "[strands-" in nodeid
            else None
        )
    elif family == "agent_sdks":
        owner = (
            "openai-agents"
            if "[openai_agents_scenarios-" in nodeid
            else "claude-agent"
            if "[claude_agent_scenarios-" in nodeid
            else "interop"
            if "[mega_native_scenarios-" in nodeid
            else None
        )
    elif family == "frameworks":
        owner = next(
            (s for s in ("llamaindex", "agno", "smolagents") if f"-{s}-" in nodeid),
            None,
        )
    else:
        owner = None
    if owner is None:
        raise ValueError(f"Unassigned Python test: {nodeid}")
    return "interop" if owner in HOME and HOME[owner] != profile else owner
