class ModelUnavailableError(RuntimeError):
    """Model AI tidak ditemukan atau gagal dimuat."""


class EmbeddingValidationError(ValueError):
    """Embedding tidak valid (dimensi, nilai, atau versi model)."""
