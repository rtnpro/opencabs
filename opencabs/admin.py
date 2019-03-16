import json

from django.contrib import admin
from django.conf.urls import url
from django.core.mail import send_mail
from django.conf import settings
from django.db import models
from django import forms
from django.contrib.contenttypes.admin import GenericTabularInline

from import_export import resources
from import_export.admin import ExportMixin
from import_export import fields
from import_export.widgets import JSONWidget

from finance.models import Payment

from utils import import_path

from .models import (Booking, Place, Rate, VehicleCategory, VehicleFeature,
                     Vehicle, Driver, VehicleRateCategory, BookingVehicle)
from .models import (BOOKING_TYPE_CHOICES_DICT,
                     BOOKING_STATUS_CHOICES_DICT,
                     BOOKING_PAYMENT_STATUS_CHOICES_DICT)
from .views import booking_invoice


class BookingResource(resources.ModelResource):
    booking_type = fields.Field()
    vehicles = fields.Field()
    source = fields.Field()
    destination = fields.Field()
    vehicle_type = fields.Field()
    status = fields.Field()
    payment_status = fields.Field()
    payments = fields.Field(widget=JSONWidget())

    class Meta:
        model = Booking
        fields = ('id', 'booking_id', 'source', 'destination',
                  'booking_type', 'customer_name', 'customer_mobile',
                  'customer_email', 'created',
                  'travel_date', 'travel_time',
                  'pickup_point', 'ssr', 'status', 'vehicle_type',
                  'vehicle_count', 'vehicles',
                  'total_fare',
                  'payment_status', 'payment_done', 'payment_due',
                  'fare_details',
                  'payments'
                )
        export_order = ('id', 'booking_id', 'source', 'destination',
                        'booking_type', 'customer_name', 'customer_mobile',
                        'customer_email', 'created',
                        'travel_date', 'travel_time',
                        'pickup_point', 'ssr', 'status', 'vehicle_type',
                        'vehicle_count', 'vehicles',
                        'total_fare',
                        'payment_status', 'payment_done', 'payment_due',
                        'fare_details',
                        'payments'
                        )

    def dehydrate_driver(self, booking):
        return str(booking.driver) if booking.driver else ''

    def dehydrate_source(self, booking):
        return booking.source.name

    def dehydrate_destination(self, booking):
        return booking.destination.name

    def dehydrate_vehicle_type(self, booking):
        return booking.vehicle_type.name

    def dehydrate_vehicle_count(self, booking):
        return booking.vehicle_count

    def dehydrate_vehicles(self, booking):
        return ','.join([
            str(i) for i in booking.bookingvehicle_set.all()] or ['x'])

    def dehydrate_driver_paid(self, booking):
        return str(booking.driver_paid)

    def dehydrate_status(self, booking):
        return BOOKING_STATUS_CHOICES_DICT.get(booking.status)

    def dehydrate_payment_status(self, booking):
        return BOOKING_PAYMENT_STATUS_CHOICES_DICT.get(booking.payment_status)

    def dehydrate_booking_type(self, booking):
        return BOOKING_TYPE_CHOICES_DICT.get(booking.booking_type)

    def dehydrate_payments(self, booking):
        return json.dumps([
            {'amount': p.type * float(p.amount), 'mode': p.mode,
             'reference_id': p.reference_id, 'comment': p.comment,
             'invoice_id': p.invoice_id, 'timestamp': str(p.timestamp)}
            for p in booking.payments.all()
        ])



class PaymentInline(GenericTabularInline):
    model = Payment
    extra = 1
    ct_field = 'item_content_type'
    ct_fk_field = 'item_object_id'
    can_delete = False
    exclude = ('details',)

    def get_readonly_fields(self, request, obj=None):
        fields = ['invoice_id', 'created_by']
        if not request.user.has_perm('finance.verify_payment'):
            fields.extend(['accounts_verified', 'accounts_verified_timestamp'])
        return fields


class BookingVehicleInline(admin.TabularInline):
    model = BookingVehicle
    extra = 1
    can_delete = True
    formfield_overrides = {
        models.TextField: {'widget': forms.Textarea(
            attrs={'rows': 3, 'cols': 50})},
        models.CharField: {'widget': forms.TextInput(attrs={'width': '10em'})}
    }
    verbose_name = 'Vehicle'
    verbose_name_plural = 'Vehicles'

    def save_model(self, request, obj, form, change):
        pass


@admin.register(Booking)
class BookingAdmin(ExportMixin, admin.ModelAdmin):
    list_display = ('booking_id', 'payment_method', 'customer_name', 'customer_mobile',
                    'source', 'destination', 'booking_type',
                    'travel_date', 'travel_time', 'vehicle_type',
                    'vehicle_count', 'vehicles',
                    'status', 'total_fare', 'payment_done', 'payment_status',
                    'payment_due', 'passengers', 'created',)
    list_filter = ('booking_type', 'status', 'travel_date',
                   'created', 'payment_status', 'payment_method')
    search_fields = ('booking_id', 'customer_name', 'customer_mobile',
                     'travel_date')
    readonly_fields = ('total_fare', 'payment_due', 'payment_done',
                       'payment_status', 'revenue',
                       'last_payment_date',)
    formfield_overrides = {
        models.TextField: {'widget': forms.Textarea(
            attrs={'rows': 3, 'cols': 30})}
    }
    inlines = (BookingVehicleInline, PaymentInline,)
    fieldsets = (
        (
            'Customer details', {
                'fields': (
                    ('customer_name', 'customer_mobile', 'customer_email'),
                    ('pickup_point', 'ssr')
                ),
            }
        ),
        (
            'Travel details', {
                'fields': (
                    ('source', 'destination', 'travel_date', 'travel_time', 'passengers'),
                    ('booking_type', 'vehicle_type', 'vehicle_count'),
                    ('status', 'distance')
                )
            }
        ),
        (
            'Payment details', {
                'fields': (
                    ('total_fare', 'payment_done', 'payment_due', 'revenue'),
                    ('last_payment_date', 'payment_method', 'payment_status', 'fare_details'),
                )
            }
        )
    )
    resource_class = import_path(settings.BOOKING_RESOURCE_CLASS)

    def vehicles(self, obj):
        return ', '.join(['{}/{}'.format(i.driver or '-', i.vehicle or '-') for i in obj.bookingvehicle_set.all()] or ['x'])

    def get_form(self, *args, **kwargs):
        form = super().get_form(*args, **kwargs)
        form.base_fields['fare_details'].widget.attrs = {
            'cols': 60, 'rows': 3}
        return form

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = self.readonly_fields
        return readonly_fields

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        status = form.cleaned_data['status']
        if 'status' in form.changed_data:
            obj.send_trip_status_to_customer()

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        if str(formset.model).find('BookingVehicle') >= 0:
            for obj, fields in formset.changed_objects:
                if ('vehicle' in fields or
                        'driver' in fields or
                        'extra_info' in fields):
                    obj.send_trip_details_to_customer()

                if 'driver' in fields:
                    if obj.driver:
                        obj.send_trip_details_to_driver()

            for obj in formset.new_objects:
                if obj.vehicle or obj.driver or obj.extra_info:
                    obj.send_trip_details_to_customer()

                if obj.driver:
                    obj.send_trip_details_to_driver()
        if 'Payment' in str(formset.model):
            send_payment_notification = False
            for obj in formset.new_objects:
                obj.created_by = request.user
                obj.save()
                if obj.payment_method == 'PG' and obj.status == 'WAT':
                    send_payment_notification = True

            for obj, fields in formset.changed_objects:
                if ('payment_method' in fields or 'status' in fields) and \
                        (obj.payment_method == 'PG' and obj.status == 'WAT'):
                    send_payment_notification = True
            if send_payment_notification:
                obj.bookings.first().send_payment_notification()


@admin.register(BookingVehicle)
class BookingVehicle(admin.ModelAdmin):
    search_fields = ('booking__booking_id', 'driver__name', 'vehicle__number')

class Account(Booking):
    class Meta:
        proxy = True


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ('booking_id', 'accounts_verified', 'payment_status',
                    'payment_done', 'payment_due',
                    'last_payment_date', 'revenue',)
    list_editable = ('accounts_verified',)
    fields = ('booking_id', 'accounts_verified',
              'payment_status', 'payment_done', 'payment_due',
              'last_payment_date', 'revenue',)
    readonly_fields = ('booking_id', 'payment_done',
                       'last_payment_date', 'revenue', 'payment_status',
                       'payment_due')
    list_filter = ('accounts_verified', 'last_updated', 'created')
    search_fields = ('booking_id',)


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    search_fields = ('name',)


@admin.register(Rate)
class RateAdmin(admin.ModelAdmin):
    list_display = ('source', 'destination', 'vehicle_category',
                    'oneway_price', 'roundtrip_price')
    list_filter = ('vehicle_category',)
    search_fields = ('source', 'destination',)


@admin.register(VehicleRateCategory)
class VehicleRateCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'tariff_per_km', 'tariff_after_hours')
    list_filter = ('features',)
    search_fields = ('name',)


@admin.register(VehicleCategory)
class VehicleCategoryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = ('name', 'mobile')
    search_fields = ('name', 'mobile')


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ('name', 'number', 'category', 'driver')
    search_fields = ('name', 'number', 'driver')
    list_filter = ('category__name',)
    search_fields = ('name', 'number')

admin.site.register(VehicleFeature)
