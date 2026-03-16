# app/routes/simulator.py
#
# Phase 1 simulator endpoints — removed before production.
#
# These exist so the React UI can open a real page when the user clicks a
# redirect button. They are not part of the production API — removed
# when the real Health / Food modules have their own URLs.
#
# Endpoints:
#   GET /api/v1/simulator/health
#   GET /api/v1/simulator/food
#
# Security: all query-param values are HTML-escaped before insertion.

import html as _html
import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/simulator", tags=["simulator"])


@router.get("/health", response_class=HTMLResponse, summary="Health module simulator")
async def health_simulator(
    query: str = "",
    urgency: str = "low",
    pet_id: str = "",
    pet_summary: str = "",
) -> HTMLResponse:
    """Phase 1 simulator — shows the pre-filled query and pet context a real Health module would receive."""
    safe_query = _html.escape(query)
    safe_urgency = _html.escape(urgency)
    safe_summary = _html.escape(pet_summary)
    color = {"high": "#ef4444", "medium": "#f97316"}.get(urgency, "#6b7280")
    html_body = f"""<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;padding:2rem;max-width:640px;margin:auto">
  <h2>Health Assistant <span style="font-size:0.9rem;color:#888">(Phase 1 Simulator)</span></h2>
  <p><b>Pet ID:</b> {_html.escape(pet_id) or '<i>not provided</i>'}</p>
  <p><b>Urgency:</b> <span style="color:{color};font-weight:bold">{safe_urgency.upper()}</span></p>
  <p><b>Pre-filled query:</b></p>
  <div style="background:#f5f5f5;padding:1rem;border-radius:8px;margin-bottom:1rem">{safe_query}</div>
  <p><b>Pet context received:</b></p>
  <div style="background:#f5f5f5;padding:1rem;border-radius:8px;white-space:pre-wrap">{safe_summary}</div>
  <hr>
  <p style="color:#aaa;font-size:0.8rem">Phase 1 simulator — real Health Module in production</p>
</body>
</html>"""
    return HTMLResponse(content=html_body)


@router.get("/food", response_class=HTMLResponse, summary="Food module simulator")
async def food_simulator(
    query: str = "",
    urgency: str = "low",
    pet_id: str = "",
    pet_summary: str = "",
) -> HTMLResponse:
    """Phase 1 simulator — shows the pre-filled query and pet context a real Food module would receive."""
    safe_query = _html.escape(query)
    safe_summary = _html.escape(pet_summary)
    html_body = f"""<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;padding:2rem;max-width:640px;margin:auto">
  <h2>Food Specialist <span style="font-size:0.9rem;color:#888">(Phase 1 Simulator)</span></h2>
  <p><b>Pet ID:</b> {_html.escape(pet_id) or '<i>not provided</i>'}</p>
  <p><b>Pre-filled query:</b></p>
  <div style="background:#f5f5f5;padding:1rem;border-radius:8px;margin-bottom:1rem">{safe_query}</div>
  <p><b>Pet context received:</b></p>
  <div style="background:#f5f5f5;padding:1rem;border-radius:8px;white-space:pre-wrap">{safe_summary}</div>
  <hr>
  <p style="color:#aaa;font-size:0.8rem">Phase 1 simulator — real Food Module in production</p>
</body>
</html>"""
    return HTMLResponse(content=html_body)
