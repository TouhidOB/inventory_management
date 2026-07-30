from .customer_dues import CustomerDuesImporter
from .products import ProductImporter
from .supplier_dues import SupplierDuesImporter

IMPORTERS = {
    "products": ProductImporter,
    "supplier_dues": SupplierDuesImporter,
    "customer_dues": CustomerDuesImporter,
}


def get_importer(import_type):
    cls = IMPORTERS.get(import_type)
    if cls is None:
        raise KeyError(f"Unknown import type: {import_type}")
    return cls()
