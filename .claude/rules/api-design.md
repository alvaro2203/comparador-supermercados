---
paths:
  - "api/**/*.py"
---

# FastAPI & API Design Rules

- **Validation:** All request bodies, query parameters, and response models must be validated using **Pydantic v2**.
- **Strict Typing:** All route handlers, dependencies, and helper functions must be fully type-hinted and pass `mypy --strict` checks without using `Any`.
- **Code Documentation:** Strictly forbid inline comments. Use Google-style docstrings exclusively for documenting classes and functions.
- **Asynchronous Handlers:** Use `async def` for all endpoints involving database operations or I/O.
- **Error Responses:** Use custom domain/application exceptions handled globally via FastAPI exception handlers to return consistent HTTP error status codes and structures.