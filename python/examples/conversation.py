"""Optional custom entry point; framework OTel entry spans can serve this role."""

from openai import OpenAI

from confident_trace import init, span, update_trace

init()
client = OpenAI()


@span
def respond(thread_id, message):
    update_trace(thread_id=thread_id)
    response = client.responses.create(model="gpt-4.1-mini", input=message)
    return response.output_text


# Each invocation's spans form a trace. Both traces belong to this conversation.
print(respond("conversation-42", "Hello"))
print(respond("conversation-42", "Another question"))
