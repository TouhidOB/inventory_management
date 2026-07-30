from purchasing.models import Supplier

from .party_dues import PartyDuesImporter


class SupplierDuesImporter(PartyDuesImporter):
    import_type = "supplier_dues"
    model = Supplier
