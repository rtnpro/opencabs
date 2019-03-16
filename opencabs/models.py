from django.db import models
from django.conf import settings
from django.core import urlresolvers
from django.core.exceptions import ValidationError
from django.contrib.contenttypes.fields import GenericRelation
from django.urls import reverse
from django.utils import timezone
from django.core.mail import send_mail

from finance.models import Payment

import json
import os
import uuid
from io import StringIO
from hashlib import md5
from datetime import datetime
from collections import OrderedDict

from utils.pdf import draw_pdf

from .notification import send_sms


class VehicleFeature(models.Model):
    name = models.CharField(max_length=30)
    description = models.TextField(max_length=100, blank=True, default='')

    created = models.DateTimeField(auto_now_add=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True, blank=True)

    def __str__(self):
        return self.name


class VehicleCategory(models.Model):
    name = models.CharField(max_length=100, unique=True, db_index=True)
    description = models.TextField(max_length=200, blank=True, default='')
    image = models.ImageField(
        upload_to='vehicles',
        max_length=100, null=True, blank=True)

    passengers = models.IntegerField(default=4, blank=True)

    def __str__(self):
        return self.name


class VehicleRateCategory(models.Model):
    name = models.CharField(max_length=30, db_index=True, unique=True)
    description = models.TextField(max_length=200, blank=True, default='')
    category = models.ForeignKey(VehicleCategory, db_index=True,
                                 on_delete=models.PROTECT)
    features = models.ManyToManyField(VehicleFeature, blank=True)
    tariff_per_km = models.PositiveIntegerField()
    tariff_after_hours = models.PositiveIntegerField()

    created = models.DateTimeField(auto_now_add=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True, blank=True)

    class Meta:
        unique_together = ('name', 'category')

    def __str__(self):
        return self.name


class Place(models.Model):
    name = models.CharField(max_length=100, db_index=True, unique=True)

    created = models.DateTimeField(auto_now_add=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True, blank=True)

    def __str__(self):
        return self.name


class Rate(models.Model):
    source = models.ForeignKey(Place, on_delete=models.PROTECT,
                               related_name='rate_source')
    destination = models.ForeignKey(Place, on_delete=models.PROTECT,
                                    related_name='rate_destination')
    vehicle_category = models.ForeignKey(VehicleRateCategory,
                                         on_delete=models.PROTECT,
                                         related_name='rate', db_index=True)
    oneway_price = models.PositiveIntegerField()
    oneway_distance = models.PositiveIntegerField(default=0, blank=True)
    oneway_driver_charge = models.PositiveIntegerField()

    roundtrip_price = models.PositiveIntegerField(blank=True, default=0)
    roundtrip_distance = models.PositiveIntegerField(blank=True, default=0)
    roundtrip_driver_charge = models.PositiveIntegerField(
        blank=True, default=0)

    code = models.CharField(max_length=100, editable=False, blank=True,
                            db_index=True)

    created = models.DateTimeField(auto_now_add=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True, blank=True)

    class Meta:
        unique_together = ('code', 'vehicle_category')

    def __str__(self):
        return '{}-{}'.format(self.source, self.destination)

    def save(self, *args, **kwargs):
        self.code = settings.ROUTE_CODE_FUNC(
            self.source.name, self.destination.name)
        if not self.roundtrip_price:
            self.roundtrip_price = 2 * self.oneway_price
        if not self.roundtrip_driver_charge:
            self.roundtrip_driver_charge = 2 * self.oneway_driver_charge
        if not self.roundtrip_distance:
            self.roundtrip_distance = 2 * self.roundtrip_distance
        super().save(*args, **kwargs)

    @property
    def total_oneway_price(self):
        return int(round(self.oneway_price * (1 + self.tax_rate)))

    @property
    def total_roundtrip_price(self):
        return int(round(self.roundtrip_price * (1 + self.tax_rate)))

    @property
    def tax_rate(self):
        if getattr(self, '_tax_rate', None):
            return self._tax_rate
        self._tax_rate = sum(
            [v['rate'] for v in settings.TAXES.values()])
        return self._tax_rate



class Driver(models.Model):
    name = models.CharField(max_length=100, db_index=True)
    mobile = models.CharField(max_length=20, unique=True, db_index=True)

    def __str__(self):
        return '{}/{}'.format(self.name, self.mobile)


class Vehicle(models.Model):
    name = models.CharField(max_length=100, db_index=True)
    number = models.CharField(max_length=20, unique=True, db_index=True)
    category = models.ForeignKey(
        VehicleCategory,
        on_delete=models.PROTECT)
    driver = models.OneToOneField(
        Driver,
        on_delete=models.PROTECT,
        unique=True,
        null=True,
        blank=True
    )

    def __str__(self):
        return '{}/{}'.format(self.name, self.number)


BOOKING_TYPE_CHOICES_DICT = getattr(
    settings, 'BOOKING_TYPE_CHOICES_DICT', OrderedDict(
        (
            ('OW', 'One way'),
            ('RT', 'Round trip')
        )
    )
)

BOOKING_STATUS_CHOICES_DICT = getattr(
    settings, 'BOOKING_STATUS_CHOICES_DICT', OrderedDict(
        (
            ('0', 'Request'),
            ('1', 'Confirmed'),
            ('2', 'Declined'),
            ('3', 'Attempt'),
        )
    )
)

BOOKING_PAYMENT_STATUS_CHOICES_DICT = getattr(
    settings, 'BOOKING_PAYMENT_STATUS_CHOICES_DICT', OrderedDict(
        (
            ('NP', 'Not paid'),
            ('PR', 'Partial'),
            ('PD', 'Paid'),
        )
    )
)

BOOKING_PAYMENT_METHOD_CHOICES_DICT = getattr(
    settings, 'BOOKING_PAYMENT_METHOD_CHOICES_DICT', OrderedDict(
        (
            ('POA', 'Pay on arrival'),
            ('ONL', 'Online'),
            ('', ''),
        )
    )
)


class Booking(models.Model):
    source = models.ForeignKey(Place, on_delete=models.PROTECT,
                               related_name='booking_source')
    destination = models.ForeignKey(Place, on_delete=models.PROTECT,
                                    related_name='booking_destination')
    pickup_point = models.TextField(max_length=200, blank=True, default="")
    booking_type = models.CharField(choices=BOOKING_TYPE_CHOICES_DICT.items(),
                                    max_length=2)
    travel_date = models.DateField()
    travel_time = models.TimeField()
    vehicle_type = models.ForeignKey(VehicleRateCategory,
                                     on_delete=models.PROTECT,
                                     related_name='booking')
    vehicle_count = models.PositiveIntegerField(default=1, blank=True)
    passengers = models.IntegerField(default=1, blank=True)
    customer_name = models.CharField(max_length=100, db_index=True,
                                     verbose_name='Name')
    customer_mobile = models.CharField(max_length=20, default='', blank=True,
                                       db_index=True, verbose_name='Mobile')
    customer_email = models.EmailField(default='', blank=True, db_index=True,
                                       verbose_name='Email')
    ssr = models.TextField(verbose_name='Special service request',
                           max_length=200, blank=True, default="",
                           help_text="Special service request")

    status = models.CharField(choices=BOOKING_STATUS_CHOICES_DICT.items(),
                              max_length=1,
                              default='0')
    payment_method = models.CharField(
        choices=BOOKING_PAYMENT_METHOD_CHOICES_DICT.items(), max_length=3,
        blank=True, null=True, default=''
    )
    payment_status = models.CharField(
        choices=BOOKING_PAYMENT_STATUS_CHOICES_DICT.items(), max_length=3,
        blank=True, null=True, default='NP')
    payment_done = models.PositiveIntegerField(blank=True, default=0)
    payment_due = models.PositiveIntegerField(blank=True, default=0)
    revenue = models.IntegerField(blank=True, default=0)
    last_payment_date = models.DateTimeField(blank=True, null=True)
    accounts_verified = models.BooleanField(default=False, db_index=True)
    payments = GenericRelation(Payment,
                               content_type_field='item_content_type',
                               object_id_field='item_object_id',
                               related_query_name='bookings')

    booking_id = models.CharField(max_length=20, blank=True, editable=False,
                                  db_index=True, unique=True)

    total_fare = models.PositiveIntegerField(blank=True, default=0)
    fare_details = models.TextField(blank=True, default="{}")
    distance = models.PositiveIntegerField(blank=True, null=True)

    created = models.DateTimeField(auto_now_add=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True, blank=True)

    def __str__(self):
        return self.booking_id

    @property
    def booking_type_display(self):
        return BOOKING_TYPE_CHOICES_DICT.get(self.booking_type)

    @property
    def humanized_payment_method(self):
        return BOOKING_PAYMENT_METHOD_CHOICES_DICT.get(self.payment_method)

    @property
    def humanized_payment_status(self):
        return BOOKING_PAYMENT_STATUS_CHOICES_DICT.get(self.payment_status)

    def get_admin_url(self):
        return urlresolvers.reverse("admin:%s_%s_change" % (self._meta.app_label, self._meta.model_name), args=(self.id,))

    def list_waiting_payments(self):
        return self.payments.filter(mode='PG', status='WAT')

    def save(self, *args, **kwargs):
        if not self.customer_email and not self.customer_mobile:
            raise ValidationError('Either of customer email and mobile is '
                                  'mandatory.')
        if self.id is None:
            self.booking_id = self._create_booking_id()
            rate = self.vehicle_type.rate.get(
                code=settings.ROUTE_CODE_FUNC(self.source.name,
                                              self.destination.name))
            fare_details = {
                'tariff_per_km': self.vehicle_type.tariff_per_km,
                'after_hour_charges': self.vehicle_type.tariff_after_hours,
                'price': (rate.oneway_price if self.booking_type == 'OW' else
                          rate.roundtrip_price),
                'driver_charge': (rate.oneway_driver_charge
                                  if self.booking_type == 'OW' else
                                  rate.roundtrip_driver_charge),
                'discount': 0,
                'markup': 0
            }
        else:
            fare_details = json.loads(self.fare_details)

        if timezone.now().timestamp() >= datetime.strptime(
                settings.EXTRA_TAXES_FROM_DATETIME,
                settings.DATETIME_STR_FORMAT).timestamp():
            fare_details['taxes'] = {
                k: v['rate'] * fare_details[settings.TAXABLE_FIELD]
                for k, v in settings.TAXES.items()}
            fare_details['taxes']['total'] = sum(
                [fare_details['taxes'][k] for k in settings.TAXES])
            fare_details['total'] = fare_details['price'] + fare_details[
                'taxes']['total']
        else:
            fare_details['total'] = fare_details['price']
        fare_details['total'] += fare_details.get('markup', 0) - \
            fare_details.get('discount', 0)
        self.total_fare = fare_details['total']
        self.payment_due = int(round(self.total_fare)) - int(
            round(self.payment_done))
        self.fare_details = json.dumps(fare_details)

        self.update_payment_summary()

        super().save(*args, **kwargs)

    def _create_booking_id(self):
        text = '{}-{}-{}-{}-{}-{}-{}-{}'.format(
            self.source, self.destination, self.booking_type,
            self.travel_date, self.travel_time,
            self.customer_name, self.customer_mobile,
            uuid.uuid1())
        return (settings.BOOKING_ID_PREFIX + md5(
            text.encode('utf-8')).hexdigest()[:8]).upper()

    def update_payment_summary(self):
        payment_done = 0
        expenses = 0
        last_payment_date = None

        for payment in self.payments.all().order_by('timestamp'):
            if payment.mode == 'PG' and payment.status != 'SUC':
                continue
            if payment.type == 1:
                payment_done += payment.amount.amount
            else:
                expenses += payment.amount.amount
            last_payment_date = payment.timestamp

        self.payment_done = payment_done

        self.last_payment_date = last_payment_date
        self.payment_due = int(round(self.total_fare)) - int(
            round(self.payment_done))
        if self.payment_done == 0:
            self.payment_status = 'NP'
        elif self.payment_due > 0:
            self.payment_status = 'PR'
        else:
            self.payment_status = 'PD'

        self.revenue = payment_done - expenses

    def pay_to_driver(self):
        fare_details = json.loads(self.fare_details)
        payment, created = Payment.objects.get_or_create(
            item_content_type__app_label='opencabs',
            item_content_type__model='booking',
            item_object_id=self.id,
            comment__startswith='Paid to driver',
            defaults={
                'item_object': self,
                'amount': fare_details['driver_charge'],
                'comment': "Paid to driver: %s" % self.driver,
                'type': -1
            }
        )
        self.driver_pay = fare_details['driver_charge']
        self.driver_invoice_id = payment.invoice_id

    def invoice(self):
        customer_details = [self.customer_name, self.customer_mobile,
                            self.customer_email]
        fare_details = json.loads(self.fare_details)
        if 'taxes' not in fare_details:
            fare_details['taxes'] = {
                'SGST': 0,
                'CGST': 0
            }
        booking_items = [{
            'description': (
                '<b>From</b>: {source}   <b>Drop</b>: {destination}<br />\n'
                '<b>Travel date & time</b>: {travel_date} {travel_time}<br />\n'
                '<b>Vehicle</b>: {vehicle_type} X <b>{vehicle_count}</b>, <b>Booking type</b>: {booking_type}\n'
            ).format(
                source=self.source,
                destination=self.destination,
                travel_date=self.travel_date.strftime('%d %b %Y'),
                travel_time=self.travel_time.strftime('%I:%M %p'),
                vehicle_type=self.vehicle_type,
                booking_type=BOOKING_TYPE_CHOICES_DICT.get(self.booking_type),
                vehicle_count=self.vehicle_count
            ),
            'amount': fare_details['price'] + fare_details.get('markup', 0)
        }]
        if self.pickup_point:
            booking_items[0]['description'] += '\n<b>Pickup point</b>: {}'.format(
                self.pickup_point)
        total_amount = fare_details.get('total') or fare_details.get('price')
        paid = self.payment_done
        due = self.payment_due
        discount = fare_details.get('discount', 0)
        f = open('/tmp/oc-booking-invoice-{}.pdf'.format(self.booking_id),
                 'wb')
        draw_pdf(f, {'id': self.booking_id,
                     'date': self.last_payment_date or self.created,
                     'customer_details': customer_details,
                     'items': booking_items,
                     'sgst': fare_details['taxes']['SGST'],
                     'cgst': fare_details['taxes']['CGST'],
                     'total_amount': total_amount,
                     'discount': discount,
                     'paid': paid,
                     'due': due,
                     'business_name': settings.INVOICE_BUSINESS_NAME,
                     'address': settings.INVOICE_BUSINESS_ADDRESS,
                     'footer': settings.INVOICE_FOOTER
                    })
        f.close()
        return f.name

    def send_payment_notification(self):
        if not settings.SEND_CUSTOMER_SMS:
            return
        msg = (
            "Payment link(s) has been generated for your booking ID: {}"
            ".\nYou can make the payment from {}"
        ).format(self.booking_id,
                 reverse('booking_details') + '?bookingid=' + self.booking_id)
        if self.customer_mobile:
            send_sms([self.customer_mobile], msg)
        if self.customer_email:
            subject = 'Payment link'
            send_mail(subject, msg, settings.FROM_EMAIL,
                      [self.customer_email])


    def send_trip_status_to_customer(self):
        if not settings.SEND_CUSTOMER_SMS:
            return
        subject = ''
        if self.status == '0':
            msg = (
                "You booking request is being processed.")
            subject = 'Booking under process'
        elif self.status == '1':
            msg = (
                "Your booking with ID: {} has been confirmed.\n"
                "You'll be notified about vehicle & driver details "
                "a few hours before your trip."
            ).format(self.booking_id)
            subject = 'Booking confirmed'
        elif self.status == '2':
            msg = (
                "Your booking with ID: {} has been declined."
            ).format(self.booking_id)
            subject = 'Booking declined'
        if self.customer_mobile:
            send_sms([self.customer_mobile], msg)
        if self.customer_email:
            send_mail(subject, msg,
                      settings.FROM_EMAIL,
                      [self.customer_email])

    def send_booking_request_ack_to_customer(self):
        if not settings.SEND_CUSTOMER_SMS:
            return
        try:
            msg = ("Dear customer,\n"
                   "We've received your booking request with ID: {}\n"
                   "You'll receive a notification when your booking "
                   "is confirmed!").format(self.booking_id)
            if self.customer_mobile:
                send_sms([self.customer_mobile], msg)
            if self.customer_email:
                send_mail('Booking request received', msg,
                          settings.FROM_EMAIL, [self.customer_email])
        except Exception as e:
            print("SMS Error: %s" % e)

    def confirm(self):
        self.status = '1'
        self.save()
        self.send_trip_status_to_customer()

    def request(self):
        self.status = '0'
        self.save()
        self.send_trip_status_to_customer()


class BookingVehicle(models.Model):
    booking = models.ForeignKey(Booking)
    driver_paid = models.BooleanField(default=False)
    driver_pay = models.PositiveIntegerField(blank=True, default=0)
    driver_invoice_id = models.CharField(max_length=50, blank=True)

    vehicle = models.ForeignKey(Vehicle, blank=True, null=True,
                                on_delete=models.PROTECT)
    driver = models.ForeignKey(Driver, blank=True, null=True,
                               on_delete=models.PROTECT)
    extra_info = models.TextField(blank=True, default='')

    def __str__(self):
        return '{}/{}/{}'.format(self.booking, self.vehicle, self.driver)

    def send_trip_details_to_customer(self):
        if not settings.SEND_CUSTOMER_SMS:
            return
        msg = ("Trip details for booking ID: {}\n"
               "Datetime: {} {}\n").format(
                   self.booking.booking_id,
                   self.booking.travel_date.strftime('%d %b, %Y'),
                   self.booking.travel_time.strftime('%I:%M %p')
               )
        if self.vehicle and self.driver:
            msg += (
                "Vehicle: {} ({})\n"
                "Driver: {}, {}"
            ).format(self.vehicle.name, self.vehicle.number,
                     self.driver.name, self.driver.mobile)
        else:
            msg += self.extra_info or ""
            msg += "\nVehicle/driver assignment pending."
        if self.booking.customer_mobile:
            send_sms([self.booking.customer_mobile], msg)
        if self.booking.customer_email:
            send_mail('Trip details',
                      msg, settings.FROM_EMAIL,
                      [self.booking.customer_email])

    def send_trip_details_to_driver(self):
        if not settings.SEND_DRIVER_SMS:
            return
        msg = (
            "Trip details for {booking_id}\n"
            "{customer_name}, {customer_mobile}\n"
            "on {travel_datetime}\n"
            "from {source} to {destination}, "
            "{booking_type_display}\n"
            "Pickup: {pickup_point}"
        ).format(
            booking_id=self.booking.booking_id,
            customer_name=self.booking.customer_name,
            customer_mobile=self.booking.customer_mobile,
            travel_datetime='{} {}'.format(
               self.booking.travel_date.strftime('%d %b, %Y'),
               self.booking.travel_time.strftime('%I:%M %p')),
            source=self.booking.source,
            destination=self.booking.destination,
            booking_type_display=self.booking.booking_type_display,
            pickup_point=self.booking.pickup_point
        )
        send_sms([self.driver.mobile], msg)
