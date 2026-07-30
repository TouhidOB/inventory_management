from crm.models import Customer

from .party_dues import PartyDuesImporter


class CustomerDuesImporter(PartyDuesImporter):
    import_type = "customer_dues"
    model = Customer
