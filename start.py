import os

import uvicorn

uvicorn.run(
    "app:app",
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8000")),
)
