"""Run the web auth app: uvicorn auth_system.app:app --reload"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("auth_system.app:app", host="0.0.0.0", port=8000, reload=True)
