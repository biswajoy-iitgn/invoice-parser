"""Test defaults: no document-parser worker and no organizer call (both need local model servers and minutes)."""
import os

os.environ.setdefault("DOC_PARSER", "off")
os.environ.setdefault("ORGANIZE", "0")
