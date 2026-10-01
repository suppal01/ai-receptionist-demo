"""Run a scripted caller through the engine with the real agent model and print the transcript.

Uses AGENT_MODEL (e.g. gemini-3.8-flash) and Google Application Default Credentials.
This calls the model API and costs a little money per run. With DATABASE_URL set, the
call is stored with source "simulator".

    AGENT_MODEL=gemini-3.8-flash GOOGLE_CLOUD_PROJECT=ai-rceptionist python -m sim.live_call
"""

import sys

from dotenv import load_dotenv

from app.agent.engine import Engine
from app.agent.model import model_from_env
from app.db.store import store_from_env

NEW_PATIENT_ALIGNERS = [
    "Hi, do you guys do Invisalign?",
    "Cool. Do you take Delta Dental?",
    "How much would aligners cost me?",
    "Sure. I'm Priya Shah, my number is 503 555 0147.",
    "Weekday mornings. Oh wait, does the office have parking?",
    "Yes, Delta Dental PPO. I'm interested in aligners.",
    "Actually it's 0 1 7 4 at the end.",
    "Yes, that's right.",
    "No, that's all.",
]


def main(lines: list[str]) -> None:
    load_dotenv()
    engine = Engine(model_from_env(), store=store_from_env(), source="simulator")
    call_id = ""
    for text in lines:
        result = engine.handle(call_id, text)
        call_id = result.call_id
        model = next((e["payload"] for e in result.events if e["type"] == "model"), None)
        cited = [c for e in result.events if e["type"] == "tool_call" for c in e["payload"].get("cited", [])]
        failed = [e["payload"] for e in result.events if e["type"] == "check" and not e["payload"]["passed"]]
        print(f"\nCALLER: {text}")
        print(f"AGENT:  {result.reply}")
        meta = f"  [stage={result.stage}"
        if model:
            meta += f" intents={model['intents']} {model['ms']}ms"
            if model["error"]:
                meta += f" ERROR={model['error']}"
        if cited:
            meta += f" cited={cited}"
        if failed:
            meta += f" failed_checks={failed}"
        storage = [e["payload"]["error"] for e in result.events if e["type"] == "storage"]
        if storage:
            meta += f" STORAGE_ERROR={storage}"
        print(meta + "]")
        if result.ended:
            break
    print(f"\nCall ID: {call_id}")
    print("Saved requests:")
    for r in engine.requests.values():
        print(" ", r.model_dump(exclude={"created_at"}))


if __name__ == "__main__":
    main(sys.argv[1:] or NEW_PATIENT_ALIGNERS)
