"""FastAPI backend for Tweak (MVP).

AI-powered instant presentation generator -- the DIY feature of Tweak.
Exposes a minimal HTTP API around the existing `slidedeckai.core.SlideDeckAI`
class to generate and download PowerPoint slide decks.
"""

import logging
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

# Make the `slidedeckai` package (under `src/`) importable without requiring
# an editable install, mirroring the approach used by `app.py`.
_SRC_DIR = Path(__file__).resolve().parent.parent / 'src'
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from dotenv import load_dotenv  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from slidedeckai.core import SlideDeckAI  # noqa: E402
from slidedeckai.global_config import GlobalConfig  # noqa: E402
from slidedeckai.helpers import llm_helper  # noqa: E402

load_dotenv()
logger = logging.getLogger(__name__)

# Default LLM to use for generation; override via the SLIDEDECKAI_MODEL env var.
DEFAULT_MODEL = os.getenv('SLIDEDECKAI_MODEL', '[gg]gemini-2.5-flash-lite')

VALID_TEMPLATE_NAMES = list(GlobalConfig.PPTX_TEMPLATE_FILES.keys())

# file_id is always a uuid4 hex string; reject anything else (e.g. path traversal).
FILE_ID_REGEX = re.compile(r'^[0-9a-f]{32}$')

# Where generated .pptx files are stored, keyed by file_id.
GENERATED_DIR = Path(__file__).resolve().parent / 'generated'
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

STATIC_DIR = Path(__file__).resolve().parent / 'static'

app = FastAPI(title='Tweak API', version='0.1.0')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


class GenerateRequest(BaseModel):
    """Request body for the /generate endpoint."""

    topic: str = Field(..., min_length=1, description='The topic of the slide deck.')
    template_idx: int = Field(0, ge=0, description='Index of the PPTX template to use.')


class GenerateResponse(BaseModel):
    """Response body for the /generate endpoint."""

    file_id: str
    download_url: str


def _get_api_key_for_model(model: str) -> str:
    """Look up the API key for a model's provider from the environment.

    Args:
        model: The model identifier, e.g. `[gg]gemini-2.5-flash-lite`.

    Returns:
        The API key, or an empty string if not configured (e.g. for Ollama).
    """
    provider, _ = llm_helper.get_provider_model(model, use_ollama=False)
    env_key_name = GlobalConfig.PROVIDER_ENV_KEYS.get(provider)
    return os.getenv(env_key_name, '') if env_key_name else ''


@app.get('/health')
def health_check():
    """Basic health check endpoint."""
    return {'status': 'ok'}


@app.get('/')
def serve_ui():
    """Serve the minimal web UI."""
    return FileResponse(STATIC_DIR / 'index.html')


@app.post('/generate', response_model=GenerateResponse)
def generate_slide_deck(request: GenerateRequest):
    """Generate a slide deck for the given topic and return a download reference."""
    if request.template_idx >= len(VALID_TEMPLATE_NAMES):
        raise HTTPException(
            status_code=400,
            detail=f'template_idx must be between 0 and {len(VALID_TEMPLATE_NAMES) - 1}.',
        )

    api_key = _get_api_key_for_model(DEFAULT_MODEL)

    slide_generator = SlideDeckAI(
        model=DEFAULT_MODEL,
        topic=request.topic,
        api_key=api_key,
        template_idx=request.template_idx,
    )

    try:
        path = slide_generator.generate()
    except Exception as ex:
        logger.exception('Failed to generate slide deck')
        raise HTTPException(status_code=502, detail=f'LLM/generation error: {ex}') from ex

    if not path:
        raise HTTPException(status_code=500, detail='Failed to generate slide deck.')

    file_id = uuid.uuid4().hex
    dest_path = GENERATED_DIR / f'{file_id}.pptx'
    shutil.move(str(path), dest_path)

    return GenerateResponse(file_id=file_id, download_url=f'/download/{file_id}')


@app.get('/download/{file_id}')
def download_slide_deck(file_id: str):
    """Download a previously generated slide deck by its file id."""
    if not FILE_ID_REGEX.match(file_id):
        raise HTTPException(status_code=400, detail='Invalid file id.')

    file_path = GENERATED_DIR / f'{file_id}.pptx'

    if not file_path.exists():
        raise HTTPException(status_code=404, detail='File not found.')

    return FileResponse(
        path=file_path,
        filename='Presentation.pptx',
        media_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
    )
