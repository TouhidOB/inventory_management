from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    """One platform field the admin maps a source column onto.

    ``kind`` drives normalization: ``text`` collapses whitespace, ``money`` and
    ``qty`` strip currency/thousands separators and parse as ``Decimal``.
    """

    name: str
    label: str
    required: bool = False
    kind: str = "text"  # text | money | qty
