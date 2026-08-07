from odoo.exceptions import AccessError
from odoo.tests import TransactionCase


class TestBostaDeliverySecurity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Delivery = cls.env["bosta.delivery"]
        cls.Item = cls.env["bosta.delivery.item"]
        cls.company_a = cls.env["res.company"].create({"name": "Bosta Delivery Security A"})
        cls.company_b = cls.env["res.company"].create({"name": "Bosta Delivery Security B"})
        cls.manager_group = cls.env.ref("bosta_integration.group_bosta_integration_manager")
        cls.user_group = cls.env.ref("bosta_integration.group_bosta_integration_user")
        cls.manager_a = cls._user("delivery-manager-a", cls.company_a, [cls.company_a], cls.manager_group)
        cls.manager_both = cls._user(
            "delivery-manager-both",
            cls.company_a,
            [cls.company_a, cls.company_b],
            cls.manager_group,
        )
        cls.integration_user = cls._user(
            "delivery-user-a",
            cls.company_a,
            [cls.company_a],
            cls.user_group,
        )
        cls._counter = 0

    @classmethod
    def _user(cls, login, company, companies, group):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login,
            "login": login,
            "email": f"{login}@example.invalid",
            "company_id": company.id,
            "company_ids": [(6, 0, [item.id for item in companies])],
            "groups_id": [(6, 0, [group.id])],
        })

    @classmethod
    def _values(cls, company, **extra):
        cls._counter += 1
        values = {
            "company_id": company.id,
            "bosta_delivery_id": f"security-delivery-{cls._counter}",
            "tracking_number": f"security-tracking-{cls._counter}",
        }
        values.update(extra)
        return values

    def test_manager_can_crud_delivery_in_allowed_company(self):
        model = self.Delivery.with_user(self.manager_a).with_context(
            allowed_company_ids=[self.company_a.id]
        )
        delivery = model.create(self._values(self.company_a))
        self.assertEqual(delivery.read(["tracking_number"])[0]["tracking_number"], delivery.tracking_number)
        delivery.write({"state_value": "Processing"})
        self.assertEqual(delivery.state_value, "Processing")
        self.assertTrue(delivery.unlink())

    def test_ordinary_user_can_read_delivery_only(self):
        delivery = self.Delivery.sudo().create(self._values(self.company_a))
        user_delivery = delivery.with_user(self.integration_user).with_context(
            allowed_company_ids=[self.company_a.id]
        )
        self.assertEqual(user_delivery.read(["tracking_number"])[0]["tracking_number"], delivery.tracking_number)
        with self.assertRaises(AccessError):
            self.Delivery.with_user(self.integration_user).with_context(
                allowed_company_ids=[self.company_a.id]
            ).create(self._values(self.company_a))
        with self.assertRaises(AccessError):
            user_delivery.write({"state_value": "forbidden"})
        with self.assertRaises(AccessError):
            user_delivery.unlink()

    def test_cross_company_reads_are_blocked(self):
        delivery_a = self.Delivery.sudo().create(self._values(self.company_a))
        delivery_b = self.Delivery.sudo().create(self._values(self.company_b))
        visible = self.Delivery.with_user(self.manager_a).with_context(
            allowed_company_ids=[self.company_a.id]
        ).search([])
        self.assertIn(delivery_a, visible)
        self.assertNotIn(delivery_b, visible)

    def test_cross_company_writes_are_blocked(self):
        delivery_b = self.Delivery.sudo().create(self._values(self.company_b))
        with self.assertRaises(AccessError):
            delivery_b.with_user(self.manager_a).with_context(
                allowed_company_ids=[self.company_a.id]
            ).write({"state_value": "forbidden"})

    def test_cross_company_create_is_blocked(self):
        with self.assertRaises(AccessError):
            self.Delivery.with_user(self.manager_a).with_context(
                allowed_company_ids=[self.company_a.id]
            ).create(self._values(self.company_b))

    def test_manager_with_both_companies_can_access_both(self):
        delivery_a = self.Delivery.sudo().create(self._values(self.company_a))
        delivery_b = self.Delivery.sudo().create(self._values(self.company_b))
        visible = self.Delivery.with_user(self.manager_both).with_context(
            allowed_company_ids=[self.company_a.id, self.company_b.id]
        ).search([])
        self.assertIn(delivery_a, visible)
        self.assertIn(delivery_b, visible)

    def test_delivery_item_security_follows_company(self):
        delivery_a = self.Delivery.sudo().create(self._values(self.company_a))
        delivery_b = self.Delivery.sudo().create(self._values(self.company_b))
        item_a = self.Item.sudo().create({"delivery_id": delivery_a.id, "title": "A"})
        item_b = self.Item.sudo().create({"delivery_id": delivery_b.id, "title": "B"})

        user_items = self.Item.with_user(self.integration_user).with_context(
            allowed_company_ids=[self.company_a.id]
        ).search([])
        self.assertIn(item_a, user_items)
        self.assertNotIn(item_b, user_items)

        with self.assertRaises(AccessError):
            self.Item.with_user(self.integration_user).with_context(
                allowed_company_ids=[self.company_a.id]
            ).create({"delivery_id": delivery_a.id, "title": "forbidden"})

    def test_manager_cannot_create_item_for_unauthorized_company(self):
        delivery_b = self.Delivery.sudo().create(self._values(self.company_b))
        with self.assertRaises(AccessError):
            self.Item.with_user(self.manager_a).with_context(
                allowed_company_ids=[self.company_a.id]
            ).create({"delivery_id": delivery_b.id, "title": "forbidden"})
