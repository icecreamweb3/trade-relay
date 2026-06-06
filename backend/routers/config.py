"""
Config router: per-user Binance API key configuration (database-backed).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from trade_relay import config as cfg_module
from trade_relay import database as db_module
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigOut(BaseModel):
    api_key: str
    api_secret: str
    testnet: bool
    mock_mode: bool

class ConfigIn(BaseModel):
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = False
    mock_mode: bool = False


@router.get("/me", response_model=ConfigOut)
def get_my_config(user: dict = Depends(get_current_user)):
    username = user["username"]
    api_key = cfg_module.get_api_key(username) or ""
    api_secret = cfg_module.get_api_secret(username) or ""
    # Mask the secret: show first 4 + asterisks + last 4
    if len(api_secret) > 8:
        api_secret_display = api_secret[:4] + "*" * (len(api_secret) - 8) + api_secret[-4:]
    else:
        api_secret_display = "*" * len(api_secret)
    return ConfigOut(
        api_key=api_key,
        api_secret=api_secret_display,
        testnet=cfg_module.is_testnet(username),
        mock_mode=cfg_module.is_mock_mode(username),
    )


@router.post("/me", response_model=ConfigOut)
def save_my_config(body: ConfigIn, user: dict = Depends(get_current_user)):
    username = user["username"]
    user_id = int(user["sub"])

    # Resolve final values: keep existing when client sends masked placeholder
    existing_key = cfg_module.get_api_key(username) or ""
    existing_secret = cfg_module.get_api_secret(username) or ""
    new_key = body.api_key.strip() if body.api_key.strip() else existing_key
    new_secret = body.api_secret.strip()
    if "***" in new_secret or not new_secret:
        new_secret = existing_secret

    db_module.update_user_api_credentials(
        user_id, new_key, new_secret,
        testnet=body.testnet, mock_mode=body.mock_mode,
    )

    if len(new_secret) > 8:
        secret_display = new_secret[:4] + "*" * (len(new_secret) - 8) + new_secret[-4:]
    else:
        secret_display = "*" * len(new_secret)

    return ConfigOut(
        api_key=new_key,
        api_secret=secret_display,
        testnet=body.testnet,
        mock_mode=body.mock_mode,
    )
