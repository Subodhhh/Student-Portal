from institute_common.appfactory import create_app
from institute_common.config import settings

from inst_paper.routes import internal, papers
from inst_paper.services import extraction


def _register_ocr() -> None:
    """Image papers are rejected unless an OCR engine is explicitly configured (OCR_ENGINE)."""
    if settings.ocr_engine == "tesseract":
        from inst_paper.services.ocr_tesseract import TesseractBackend
        extraction.set_ocr_backend(TesseractBackend())
    elif settings.ocr_engine:
        raise RuntimeError(f"Unknown OCR_ENGINE {settings.ocr_engine!r} (supported: tesseract)")


app = create_app("paper", "Institute Question and Paper Service", [papers.router], internal_routers=[internal.router],
                 on_startup=[_register_ocr])
