from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

from foodsaving.conversations import tasks, stats
from foodsaving.conversations.models import (
    ConversationParticipant, ConversationMessage, ConversationMessageReaction, ConversationThreadParticipant,
    Conversation
)
from foodsaving.pickups.models import PickupDate


@receiver(pre_save, sender=ConversationMessage)
def create_thread_participant(sender, instance, **kwargs):
    message = instance
    thread = message.thread

    if message.is_thread_reply():
        if not thread.thread_id:
            # initialize thread
            thread.participants.create(user=thread.author)
            ConversationMessage.objects.filter(id=thread.id).update(thread=thread)
            del thread.thread_id  # will be refreshed on next access

        if message.author != thread.author and not thread.participants.filter(user=message.author).exists():
            thread.participants.create(user=message.author)


@receiver(post_save, sender=ConversationMessage)
def mark_as_read(sender, instance, created, **kwargs):
    """Mark sent messages as read for the author"""
    message = instance

    if not created:
        return

    if message.is_thread_reply():
        participant = ConversationThreadParticipant.objects.get(
            user=message.author,
            thread=message.thread,
        )
    else:
        participant = ConversationParticipant.objects.get(user=message.author, conversation=message.conversation)

    participant.seen_up_to = message
    participant.save()


@receiver(post_save, sender=ConversationMessage)
def notify_participants(sender, instance, created, **kwargs):
    message = instance

    if not created:
        return

    tasks.notify_participants.schedule(args=(message, ), delay=5 * 60)


@receiver(post_save, sender=ConversationMessage)
def message_created(sender, instance, created, **kwargs):
    if not created:
        return
    stats.message_written(instance)


@receiver(post_save, sender=ConversationMessageReaction)
def reaction_created(sender, instance, created, **kwargs):
    if not created:
        return
    stats.reaction_given(instance)


@receiver(post_save, sender=ConversationParticipant)
def set_conversation_updated_at_on_create(sender, instance, created, **kwargs):
    if created:
        participant = instance
        participant.conversation.save()


@receiver(pre_delete, sender=ConversationParticipant)
def set_conversation_updated_at_on_delete(sender, instance, **kwargs):
    participant = instance
    participant.conversation.save()


@receiver(pre_save, sender=PickupDate)
def send_message_on_cancelled_pickup(sender, instance, **kwargs):
    pickup = instance

    if not pickup.id:
        return

    old = PickupDate.objects.get(id=pickup.id)
    if not pickup.cancelled_at or old.cancelled_at:
        return

    conversation = Conversation.objects.get_for_target(pickup)
    conversation.messages.create(
        author=pickup.last_changed_by,
        content=pickup.last_changed_message,
    )
