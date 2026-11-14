from django import forms
from django.conf import settings

from apps.brokers.models import BrokerProfile
from apps.core.phone import normalize_phone

DATETIME_WIDGET = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class BrokerForm(forms.Form):
    full_name = forms.CharField(max_length=150)
    phone = forms.CharField(max_length=20)
    id_checked_at = forms.DateTimeField(
        label="National ID checked at", widget=DATETIME_WIDGET, input_formats=["%Y-%m-%dT%H:%M"]
    )
    terms_signed_at = forms.DateTimeField(
        label="Broker agreement signed at", widget=DATETIME_WIDGET, input_formats=["%Y-%m-%dT%H:%M"]
    )
    areas = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, required=False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["areas"].choices = [(a, a) for a in settings.SERVICE_AREAS]

    def clean_phone(self) -> str:
        return normalize_phone(self.cleaned_data["phone"])


class BrokerStatusForm(forms.Form):
    status = forms.ChoiceField(choices=BrokerProfile.Status.choices)
    reason = forms.CharField(max_length=200)


class ManualPaymentForm(forms.Form):
    reference = forms.CharField(max_length=10, label="Booking reference (e.g. CL7K3Q9P)")
    provider_txn_id = forms.CharField(max_length=64, label="Mobile-money transaction ID")
    amount_ugx = forms.IntegerField(min_value=1, label="Amount received (UGX)")
    amount_differs = forms.BooleanField(
        required=False,
        label="Amount differs from the fee (hold for a second person to settle)",
    )

    def clean_reference(self) -> str:
        return self.cleaned_data["reference"].strip().upper()


class ResolveForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[("accept", "Accept as the fee"), ("refund", "Refund what was paid")]
    )
    note = forms.CharField(max_length=200)


class PayoutForm(forms.Form):
    payout_ref = forms.CharField(max_length=64, label="Payout transaction ID")


class ReconcileForm(forms.Form):
    statement_ref = forms.CharField(max_length=64, label="Statement line")
