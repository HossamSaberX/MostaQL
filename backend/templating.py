"""The one Jinja environment for every page, so shared values are set once."""
import os

from fastapi.templating import Jinja2Templates

# Bump when anything under /assets changes, so browsers fetch the new files.
ASSET_VERSION = 6

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
templates.env.globals["asset_version"] = ASSET_VERSION
