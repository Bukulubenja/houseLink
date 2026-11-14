from django import forms

from apps.brokers.models import BrokerProfile

from .models import Appointment, ViewingReport

DATETIME_WIDGET = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
DATETIME_FORMATS = ["%Y-%m-%dT%H:%M"]


class RequestForm(forms.Form):
    preferred_start = forms.DateTimeField(
        label="When would you like to view?", widget=DATETIME_WIDGET, input_formats=DATETIME_FORMATS
    )
    note = forms.CharField(
        max_length=300,
        required=False,
        label="Anything we should know? (optional)",
        widget=forms.Textarea(attrs={"rows": 2}),
    )


class FeedbackForm(forms.Form):
    rating = forms.TypedChoiceField(
        choices=[(n, str(n)) for n in range(5, 0, -1)], coerce=int, widget=forms.RadioSelect
    )
    comment = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), required=False, max_length=1000
    )
    may_publish = forms.BooleanField(
        required=False, label="Crib Link may share this feedback (without my phone number)."
    )


def _active_brokers():
    return BrokerProfile.objects.filter(
        status=BrokerProfile.Status.ACTIVE, user__is_active=True
    ).select_related("user")


class ConfirmForm(forms.Form):
    scheduled_start = forms.DateTimeField(widget=DATETIME_WIDGET, input_formats=DATETIME_FORMATS)
    broker = forms.ModelChoiceField(queryset=BrokerProfile.objects.none())

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["broker"].queryset = _active_brokers()


class ReassignForm(forms.Form):
    broker = forms.ModelChoiceField(queryset=BrokerProfile.objects.none(), label="New broker")

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.fields["broker"].queryset = _active_brokers()


class CloseForm(forms.Form):
    reason = forms.ChoiceField(
        choices=[c for c in Appointment.CancelReason.choices if c[0] != "customer"]
    )
    note = forms.CharField(max_length=300, required=False)


class ReportForm(forms.Form):
    customer_attended = forms.TypedChoiceField(
        choices=[("yes", "Yes"), ("no", "No")],
        coerce=lambda v: v == "yes",
        widget=forms.RadioSelect,
        label="Did the customer attend?",
    )
    property_as_listed = forms.TypedChoiceField(
        choices=[("yes", "Yes"), ("no", "No")],
        coerce=lambda v: v == "yes",
        widget=forms.RadioSelect,
        label="Did the property match the listing?",
    )
    discrepancy_note = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}), required=False, label="What didn't match?"
    )
    customer_interest = forms.ChoiceField(choices=ViewingReport.Interest.choices)
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
