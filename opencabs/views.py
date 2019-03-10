import os

from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.urls import reverse
from django.core.mail import send_mail
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse

from formtools.wizard.views import CookieWizardView

from .forms import booking as booking_form
from .models import Booking

FORMS = [
    ('itinerary', booking_form.BookingTravelForm),
    ('vehicles', booking_form.BookingVehiclesForm),
    ('contactinfo', booking_form.BookingContactInfoForm),
    ('paymentinfo', booking_form.BookingPaymentInfoForm),
]

TEMPLATES = {
    'itinerary': 'opencabs/index.html',
    'vehicles': 'opencabs/booking_vehicles.html',
    'contactinfo': 'opencabs/booking_contactinfo.html',
    'paymentinfo': 'opencabs/booking_paymentinfo.html',
}


class BookingWizard(CookieWizardView):

    form_list = FORMS

    def get_context_data(self, form, **kwargs):
        context_data = super().get_context_data(form, **kwargs)
        context_data['settings'] = settings
        return context_data

    def get_form_kwargs(self, step):
        data = {}
        if step == 'vehicles':
            itinerary_data = self.get_cleaned_data_for_step('itinerary')
            data.update({'source': itinerary_data['source'],
                         'destination': itinerary_data['destination'],
                         'booking_type': itinerary_data['booking_type']})
        return data

    def get_template_names(self):
        return [TEMPLATES[self.steps.current]]

    def done(self, form_list, form_dict, **kwargs):
        data = form_dict['itinerary'].cleaned_data
        data.update(form_dict['vehicles'].cleaned_data)
        contact_info = form_dict['contactinfo'].cleaned_data
        data.update(contact_info)
        payment_info = form_dict['paymentinfo'].cleaned_data
        data.update(payment_info)
        booking = Booking(**data)
        if booking.payment_method == 'ONL':
            booking.status = '3'
        booking.save()

        if booking.payment_method == 'ONL':
            payment = booking.payments.create(
                amount=booking.total_fare, type=1, mode='PG', status='WAT')
            return redirect(reverse('payment_start') + '?order_id=' + payment.invoice_id)
        else:
            booking.send_booking_request_ack_to_customer()
            return redirect(
                reverse('booking_details') + '?bookingid=' + booking.booking_id)

booking_wizard = BookingWizard.as_view()


def index(request):
    return render(request, 'opencabs/index.html', {
        'settings': settings,
        'wizard': booking_wizard
    })


def booking_details(request):
    booking_id = request.GET.get('bookingid', '').upper()
    booking = get_object_or_404(Booking, booking_id=booking_id)
    payment_status = ""
    order_id = request.GET.get('orderid', None)
    if order_id:
        payment = booking.payments.get(invoice_id=order_id)
        if payment.status in ['ERR', 'CAN', 'ABT', 'FAL']:
            payment_status = "failure"

    return render(request, 'opencabs/booking_details.html', {
        'settings': settings,
        'booking': booking,
        'payment_status': payment_status,
        'order_id': order_id,
        'waiting_payments': booking.list_waiting_payments()
    })


@staff_member_required
def booking_invoice(request, booking_id):
    booking = Booking.objects.get(id=booking_id)
    path = booking.invoice()
    with open(path, 'rb') as f:
        response = HttpResponse(
            content=f.read(), content_type='application/pdf')
        os.remove(f.name)
        return response
