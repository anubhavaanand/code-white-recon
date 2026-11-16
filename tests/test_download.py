import json
from unittest.mock import AsyncMock, patch

import pytest

from cli.download import download_patent_assets
from core.models import PatentRecord


@pytest.mark.asyncio
async def test_download_patent_assets_success(tmp_path):
    record = PatentRecord(
        id="US1234567A",
        title="Solid State Battery",
        assignee="QuantumTech",
        dates={"filed": "2023-01-01"},
        abstract="A novel battery structure",
        claims=["Claim 1 text"],
        image_urls=["https://patentimages.storage.googleapis.com/00/11/22/fig1.png"],
        status="ACTIVE",
        family_id="F123",
    )

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.content = b"\x89PNG\r\n\x1a\nfakeimagebytes"

    with patch("httpx.AsyncClient.get", return_value=mock_resp), \
         patch("cli.download.render_kitty_png", return_value="\x1b_Gfakekitty\x1b\\"):
        out_dir = await download_patent_assets(record, base_path=str(tmp_path))

    assert out_dir.exists()
    assert (out_dir / "metadata.json").exists()
    assert (out_dir / "figure_1.png").exists()
    assert (out_dir / "figure_1.kitty").exists()

    with open(out_dir / "metadata.json") as f:
        data = json.load(f)
        assert data["id"] == "US1234567A"


@pytest.mark.asyncio
async def test_download_blocks_unsafe_url(tmp_path, capsys):
    record = PatentRecord(
        id="US9999999A",
        title="Solar Cell",
        assignee="SunCorp",
        dates={"filed": "2022-01-01"},
        abstract="Solar cell design",
        claims=[],
        image_urls=["http://malicious.example.com/exploit.png"],
        status="ACTIVE",
        family_id="F999",
    )

    out_dir = await download_patent_assets(record, base_path=str(tmp_path))

    assert out_dir.exists()
    assert (out_dir / "metadata.json").exists()
    assert not (out_dir / "figure_1.png").exists()

    captured = capsys.readouterr()
    assert "Blocked untrusted figure URL" in captured.out
