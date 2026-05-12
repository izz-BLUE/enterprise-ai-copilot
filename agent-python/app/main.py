from fastapi import FastAPI

app = FastAPI(title="Agent Python Service")


@app.get("/agent/health")
def health():
    return {
        "service": "agent-python",
        "status": "UP"
    }