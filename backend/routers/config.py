"""
Config router: per-user Binance API key configuration (YAML-based).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from trade_relay import config as cfg_module
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
    data = cfg_module.load_user_config(username)
    return ConfigOut(
        api_key=data.get("binance", {}).get("api_key", ""),
        api_secret=data.get("binance", {}).get("api_secret", ""),
        testnet=bool(data.get("binance", {}).get("testnet", False)),
        mock_mode=bool(data.get("trading", {}).get("mock_mode", False)),
    )


@router.post("/me", response_model=ConfigOut)
def save_my_config(body: ConfigIn, user: dict = Depends(get_current_user)):
    username = user["username"]
    cfg_module.save_user_config(username, {
        "binance": {
            "api_key": body.api_key,
            "api_secret": body.api_secret,
            "testnet": body.testnet,
        },
        "trading": {
            "mock_mode": body.mock_mode,
        },
    })
    return ConfigOut(
        api_key=body.api_key,
        api_secret=body.api_secret,
        testnet=body.testnet,
        mock_mode=body.mock_mode,
    )
