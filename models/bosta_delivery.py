from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


FLOW_SELECTION = [
    ("forward", "Forward"),
    ("return_to_origin", "Return to Origin"),
    ("customer_return", "Customer Return"),
    ("other", "Other"),
]

LIFECYCLE_STAGE_SELECTION = [
    ("unknown", "Unknown"),
    ("pre_pickup", "Pre-pickup"),
    ("with_bosta", "With Bosta"),
    ("delivered_to_customer", "Delivered to Customer"),
    ("returning_to_origin", "Returning to Origin"),
    ("returned_to_origin", "Returned to Origin"),
    ("customer_return_pickup", "Customer Return Pickup"),
    ("customer_return_completed", "Customer Return Completed"),
    ("terminated", "Terminated"),
    ("lost", "Lost"),
    ("damaged", "Damaged"),
    ("ambiguous", "Ambiguous"),
]

RETURN_SCENARIO_SELECTION = [
    ("none", "None"),
    ("pre_delivery_return", "Pre-delivery Return"),
    ("post_delivery_customer_return", "Post-delivery Customer Return"),
    ("partial_return", "Partial Return"),
    ("lost", "Lost"),
    ("damaged", "Damaged"),
    ("ambiguous", "Ambiguous"),
]


class BostaDelivery(models.Model):
    _name = "bosta.delivery"
    _description = "Bosta Delivery"
    _rec_name = "tracking_number"
    _order = "bosta_updated_at desc, id desc"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
        ondelete="restrict",
    )
    bosta_delivery_id = fields.Char(
        string="Bosta Delivery ID",
        required=True,
        index=True,
        copy=False,
        help='Bosta delivery "_id".',
    )
    tracking_number = fields.Char(
        string="Tracking Number",
        required=True,
        index=True,
        copy=False,
    )

    creation_source = fields.Char(string="Creation Source", index=True)
    business_reference = fields.Char(string="Business Reference", index=True)
    unique_business_reference = fields.Char(string="Unique Business Reference", index=True)

    shopify_order_id = fields.Char(string="Shopify Order ID", index=True)
    shopify_order_number = fields.Char(string="Shopify Order Number", index=True)
    shopify_store_name = fields.Char(string="Shopify Store Name")
    shopify_created_at = fields.Datetime(string="Shopify Created At")

    delivery_type_code = fields.Integer(string="Delivery Type Code")
    delivery_type_value = fields.Char(string="Delivery Type", index=True)
    flow_type = fields.Selection(
        FLOW_SELECTION,
        string="Flow",
        compute="_compute_flow_type",
        store=True,
        index=True,
        help="Normalized flow derived only from the raw Bosta delivery type.",
    )

    state_code = fields.Integer(string="State Code", index=True)
    state_value = fields.Char(string="State", index=True)
    state_child_state = fields.Char(string="Child State")
    masked_state = fields.Char(string="Masked State")

    lifecycle_stage = fields.Selection(
        LIFECYCLE_STAGE_SELECTION,
        string="Lifecycle Stage",
        default="unknown",
        required=True,
        readonly=True,
        index=True,
        copy=False,
        help="Safe lifecycle stage derived by the Bosta lifecycle interpreter.",
    )
    return_scenario = fields.Selection(
        RETURN_SCENARIO_SELECTION,
        string="Return Scenario",
        default="none",
        required=True,
        readonly=True,
        index=True,
        copy=False,
    )
    lifecycle_rule_code = fields.Char(
        string="Lifecycle Rule",
        readonly=True,
        copy=False,
        index=True,
        help="Short non-PII deterministic rule code used for lifecycle audit/debugging.",
    )
    lifecycle_ambiguous = fields.Boolean(
        string="Lifecycle Ambiguous",
        default=False,
        readonly=True,
        copy=False,
        index=True,
    )

    bosta_created_at = fields.Datetime(string="Bosta Created At")
    bosta_updated_at = fields.Datetime(string="Bosta Updated At", index=True)
    pending_pickup_at = fields.Datetime(string="Pending Pickup At")
    collected_from_business_at = fields.Datetime(string="Collected From Business At")
    picked_up_at = fields.Datetime(string="Picked Up At")
    delivery_time = fields.Datetime(string="Delivery Time")

    receiver_bosta_id = fields.Char(string="Receiver Bosta ID", index=True)
    receiver_name = fields.Char(string="Receiver Name")
    receiver_phone = fields.Char(string="Receiver Phone", index=True)
    receiver_second_phone = fields.Char(string="Receiver Second Phone")

    dropoff_country_code = fields.Char(string="Drop-off Country Code")
    dropoff_country_name = fields.Char(string="Drop-off Country")
    dropoff_city = fields.Char(string="Drop-off City")
    dropoff_zone = fields.Char(string="Drop-off Zone")
    dropoff_district = fields.Char(string="Drop-off District")
    dropoff_first_line = fields.Char(string="Drop-off Address Line 1")
    dropoff_second_line = fields.Char(string="Drop-off Address Line 2")
    dropoff_building_number = fields.Char(string="Building Number")
    dropoff_floor = fields.Char(string="Floor")
    dropoff_apartment = fields.Char(string="Apartment")

    package_items_count = fields.Integer(string="Package Items Count")
    package_type = fields.Char(string="Package Type")
    package_size = fields.Char(string="Package Size")
    package_weight = fields.Float(string="Package Weight")

    attempts_count = fields.Integer(string="Attempts Count")
    delivery_attempts_count = fields.Integer(string="Delivery Attempts Count")
    return_attempts_count = fields.Integer(string="Return Attempts Count")
    pickup_attempts_count = fields.Integer(string="Pickup Attempts Count")

    cod_amount = fields.Float(string="COD Amount")
    original_cod_amount = fields.Float(string="Original COD Amount")

    shipment_fees = fields.Float(string="Shipment Fees")
    shipping_fee = fields.Float(string="Shipping Fee")
    bundle_discount = fields.Float(string="Bundle Discount")
    opening_package_fee = fields.Float(string="Opening Package Fee")
    bosta_material_fee = fields.Float(string="Bosta Material Fee")
    price_before_vat = fields.Float(string="Price Before VAT")
    price_after_vat = fields.Float(string="Price After VAT")
    vat_rate = fields.Float(string="VAT Rate")
    pricing_currency_code = fields.Char(string="Pricing Currency Code")

    original_delivery_id = fields.Many2one(
        "bosta.delivery",
        string="Original Delivery",
        index=True,
        ondelete="set null",
        check_company=True,
        help="Optional link to the original delivery. Linking is performed by a later phase.",
    )
    return_delivery_ids = fields.One2many(
        "bosta.delivery",
        "original_delivery_id",
        string="Return Deliveries",
    )

    item_ids = fields.One2many(
        "bosta.delivery.item",
        "delivery_id",
        string="Items",
    )
    item_count = fields.Integer(string="Item Count", compute="_compute_item_count")

    _sql_constraints = [
        (
            "bosta_delivery_company_delivery_id_unique",
            "unique(company_id, bosta_delivery_id)",
            "The Bosta delivery ID must be unique within a company.",
        ),
        (
            "bosta_delivery_company_tracking_unique",
            "unique(company_id, tracking_number)",
            "The tracking number must be unique within a company.",
        ),
    ]

    @api.depends("delivery_type_code", "delivery_type_value")
    def _compute_flow_type(self):
        for record in self:
            value = (record.delivery_type_value or "").strip().casefold()
            if record.delivery_type_code == 10 or value == "send":
                record.flow_type = "forward"
            elif record.delivery_type_code == 20 or value == "return to origin":
                record.flow_type = "return_to_origin"
            elif record.delivery_type_code == 25 or value == "customer return pickup":
                record.flow_type = "customer_return"
            else:
                record.flow_type = "other"

    @api.depends("item_ids")
    def _compute_item_count(self):
        for record in self:
            record.item_count = len(record.item_ids)

    @api.constrains("company_id", "original_delivery_id")
    def _check_original_delivery_relation(self):
        for record in self:
            original = record.original_delivery_id
            if original:
                if original == record:
                    raise ValidationError(_("A Bosta delivery cannot reference itself as its original delivery."))
                if original.company_id != record.company_id:
                    raise ValidationError(_("Original and return Bosta deliveries must belong to the same company."))

            if any(child.company_id != record.company_id for child in record.return_delivery_ids):
                raise ValidationError(_("Original and return Bosta deliveries must belong to the same company."))
