"""Controlled vocabularies — single source of truth.

These live in the app layer (not as DB enums/CHECKs) so new values require no
migration. Validate query params and request bodies against these sets.
"""
from fastapi import HTTPException, status

SERVICES = {
    "dermoscopy", "skin_biopsy", "excision", "mohs_surgery", "cryotherapy",
    "electrosurgery", "curettage", "histopathology", "immunohistochemistry",
    "total_body_photography", "teledermatology", "oncology_treatment",
}

SPECIALTIES = {
    "general_dermatology", "oncodermatology", "dermatopathology", "dermoscopy",
    "dermatologic_surgery", "surgical_oncology", "medical_oncology",
}


def validate_services(values: list[str] | None) -> list[str] | None:
    """Raise 422 if any value is not a known service."""
    if not values:
        return values
    unknown = sorted(set(values) - SERVICES)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown service(s): {unknown}. Allowed: {sorted(SERVICES)}",
        )
    return values


def validate_specialties(values: list[str] | None) -> list[str] | None:
    """Raise 422 if any value is not a known specialty."""
    if not values:
        return values
    unknown = sorted(set(values) - SPECIALTIES)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown specialty(ies): {unknown}. Allowed: {sorted(SPECIALTIES)}",
        )
    return values
