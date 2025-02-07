from dotenv import load_dotenv

import numpy as np
import gradio as gr
from fastrtc import ReplyOnPause, Stream, AdditionalOutputs
from fastrtc.utils import audio_to_bytes, aggregate_bytes_to_16bit
from pathlib import Path
from fastapi.responses import HTMLResponse, StreamingResponse
from groq import Groq
import anthropic
from elevenlabs import ElevenLabs
import os
from pydantic import BaseModel
import json

load_dotenv()

groq_client = Groq()
claude_client = anthropic.Anthropic()
tts_client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])

curr_dir = Path(__file__).parent


def response(
    audio: tuple[int, np.ndarray],
    chatbot: list[dict] | None = None,
):
    chatbot = chatbot or []
    messages = [{"role": d["role"], "content": d["content"]} for d in chatbot]
    prompt = groq_client.audio.transcriptions.create(
        file=("audio-file.mp3", audio_to_bytes(audio)),
        model="whisper-large-v3-turbo",
        response_format="verbose_json",
    ).text
    print("prompt", prompt)
    chatbot.append({"role": "user", "content": prompt})
    messages.append({"role": "user", "content": prompt})
    response = claude_client.messages.create(
        model="claude-3-5-haiku-20241022",
        max_tokens=512,
        messages=messages,
    )
    response_text = " ".join(
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text"
    )
    print("response_text", response_text)
    chatbot.append({"role": "assistant", "content": response_text})
    yield AdditionalOutputs(chatbot)
    iterator = tts_client.text_to_speech.convert_as_stream(
        text=response_text,
        voice_id="JBFqnCBsd6RMkjVDRZzb",
        model_id="eleven_multilingual_v2",
        output_format="pcm_24000"
        
    )
    for chunk in aggregate_bytes_to_16bit(iterator):
        audio_array = np.frombuffer(chunk, dtype=np.int16).reshape(1, -1)
        yield (24000, audio_array, "mono")


chatbot = gr.Chatbot(type="messages")
stream = Stream(
    modality="audio",
    mode="send-receive",
    handler=ReplyOnPause(response),
    additional_outputs_handler=lambda a: a,
    additional_inputs=[chatbot],
    additional_outputs=[chatbot],
)

class Message(BaseModel):
    role: str
    content: str


class InputData(BaseModel):
    webrtc_id: str
    chatbot: list[Message]



@stream.get("/")
async def _():
    return HTMLResponse(
        content=(curr_dir / "talk_to_claude_index.html").read_text(), status_code=200
    )


@stream.post("/input_hook")
async def _(body: InputData):
    stream.set_input(body.webrtc_id, body.model_dump()["chatbot"])
    return {"status": "ok"}


@stream.get("/outputs")
def _(webrtc_id: str):
    async def output_stream():
        async for output in stream.output_stream(webrtc_id):
            chatbot = output.args[0]
            yield f"event: output\ndata: {json.dumps(chatbot[-2])}\n\n"
            yield f"event: output\ndata: {json.dumps(chatbot[-1])}\n\n"

    return StreamingResponse(output_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    # import uvicorn

    # s = uvicorn.run(stream)

    stream.fastphone()
