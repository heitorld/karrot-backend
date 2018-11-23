from django.conf import settings
from django.db import transaction
from django.utils.translation import ugettext as _
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from foodsaving.history.models import History, HistoryTypus
from foodsaving.history.utils import get_changed_data
from foodsaving.stores.models import Store as StoreModel, StoreStatus


class StoreHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreModel
        fields = '__all__'


class StoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreModel
        fields = [
            'id',
            'name',
            'description',
            'group',
            'address',
            'latitude',
            'longitude',
            'weeks_in_advance',
            'status',
            'last_changed_message',
            'last_changed_by',
        ]

        extra_kwargs = {
            'name': {
                'min_length': 3,
            },
            'description': {
                'trim_whitespace': False,
                'max_length': settings.DESCRIPTION_MAX_LENGTH,
            },
            'last_changed_by': {
                'read_only': True,
            },
        }

    status = serializers.ChoiceField(
        choices=[status.value for status in StoreStatus], default=StoreModel.DEFAULT_STATUS
    )

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user

        store = super().create(validated_data)

        # TODO move into receiver
        History.objects.create(
            typus=HistoryTypus.STORE_CREATE,
            group=store.group,
            store=store,
            users=[
                self.context['request'].user,
            ],
            payload=self.initial_data,
            after=StoreHistorySerializer(store).data,
        )
        store.group.refresh_active_status()
        return store

    @transaction.atomic()
    def update(self, store, validated_data):
        changed_data = get_changed_data(store, validated_data)
        before_data = StoreHistorySerializer(store).data
        store = super().update(store, validated_data)
        after_data = StoreHistorySerializer(store).data

        if 'weeks_in_advance' in changed_data or \
                ('status' in changed_data and store.status == StoreStatus.ACTIVE.value):
            # TODO: move this into pickups/receivers.py
            for series in store.series.all():
                series.last_changed_by = store.last_changed_by
                series.last_changed_message = store.last_changed_message
                series.save()
                series.override_pickups()

        if before_data != after_data:
            History.objects.create(
                typus=HistoryTypus.STORE_MODIFY,
                group=store.group,
                store=store,
                users=[
                    self.context['request'].user,
                ],
                payload=changed_data,
                before=before_data,
                after=after_data,
            )
        store.group.refresh_active_status()
        return store

    def validate_group(self, group):
        if not group.is_member(self.context['request'].user):
            raise PermissionDenied(_('You are not a member of this group.'))
        if not group.is_editor(self.context['request'].user):
            raise PermissionDenied(_('You need to be a group editor'))
        return group

    def validate_weeks_in_advance(self, w):
        if w < 1:
            raise serializers.ValidationError(_('Set at least one week in advance'))
        return w

    def validate(self, data):
        data['last_changed_by'] = self.context['request'].user
        return data
