import os
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)

AGENT_API_TOKEN = os.getenv("AGENT_API_TOKEN", "")

def require_agent_token(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    if not AGENT_API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AGENT_API_TOKEN is not configured",
        )

    if not creds or creds.scheme.lower() != "bearer" or creds.credentials != AGENT_API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )