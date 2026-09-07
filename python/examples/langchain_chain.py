"""Install langchain-openai. Set OPENAI_API_KEY and CONFIDENT_API_KEY."""

import os

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

import confident_trace as ct


def main():
    ct.init()
    try:
        chain = (
            ChatPromptTemplate.from_template("Explain {topic} in one sentence.")
            | ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
            | StrOutputParser()
        )
        with ct.span("request", thread_id="example-conversation"):
            # Keep the request open while the model and parser stream.
            stream = chain.stream({"topic": "OpenTelemetry"})
            try:
                for text in stream:
                    print(text, end="", flush=True)
                print()
            finally:
                stream.close()
    finally:
        ct.shutdown()


if __name__ == "__main__":
    main()
