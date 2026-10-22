from collections import OrderedDict

from aiogram import F, Router
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender
from loguru import logger

from app.bot import texts
from app.bot.handlers.qualify import run_dialog_turn
from app.core.errors import EmptyTranscriptionError, ExternalServiceError, InvalidInputError
from app.db.models import MessageKind, User
from app.services.leads import LeadService
from app.services.media import MediaService, PhotoInput, VoiceInput
from app.services.qualification import QualificationService

MAX_TRACKED_MEDIA_GROUPS = 1000


class MediaGroupTracker:
    """Remembers recent album ids: an album gets one acknowledgement, not one per photo.

    In-process state is enough: the bot runs in one process and a user's updates are
    serialized by UserLockMiddleware.
    """

    def __init__(self, max_size: int = MAX_TRACKED_MEDIA_GROUPS) -> None:
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._max_size = max_size

    def first_seen(self, media_group_id: str | None) -> bool:
        if media_group_id is None:
            return True
        if media_group_id in self._seen:
            return False
        self._seen[media_group_id] = None
        if len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
        return True


async def voice_message(
    message: Message,
    user: User,
    media: MediaService,
    qualification: QualificationService,
    leads: LeadService,
) -> None:
    voice = message.voice
    bot = message.bot
    assert voice is not None and bot is not None  # guaranteed by the F.voice filter
    voice_input = VoiceInput(voice.file_id, voice.duration, voice.file_size, voice.mime_type)
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
            text = await media.transcribe_voice(voice_input)
    except EmptyTranscriptionError:
        await message.answer(texts.VOICE_NOT_RECOGNIZED)
        return
    except InvalidInputError:
        await message.answer(texts.VOICE_TOO_LONG)
        return
    except ExternalServiceError as exc:
        logger.warning("voice processing failed service={} status={}", exc.service, exc.status_code)
        await message.answer(texts.VOICE_FAILED)
        return

    await run_dialog_turn(
        message,
        user,
        qualification,
        leads,
        text,
        MessageKind.VOICE,
        too_long_text=texts.VOICE_TOO_LONG,
    )


async def photo_message(
    message: Message,
    user: User,
    media: MediaService,
    qualification: QualificationService,
    leads: LeadService,
    media_groups: MediaGroupTracker,
) -> None:
    assert message.photo  # guaranteed by the F.photo filter
    photo = message.photo[-1]  # the largest size
    try:
        await media.validate_photo(PhotoInput(photo.file_id, photo.file_size))
    except InvalidInputError:
        await message.answer(texts.PHOTO_INVALID)
        return
    except ExternalServiceError as exc:
        logger.warning("photo processing failed service={} status={}", exc.service, exc.status_code)
        await message.answer(texts.PHOTO_FAILED)
        return

    caption = (message.caption or "").strip()
    first_in_album = media_groups.first_seen(message.media_group_id)
    ack = texts.PHOTO_RECEIVED if not caption and first_in_album else None
    attached = await qualification.attach_photo(user, photo.file_id, ack)
    if caption:
        await run_dialog_turn(message, user, qualification, leads, caption)
    elif ack is not None:
        await message.answer(ack)
    if attached.after_delivery:
        # After the client got the answer. A photo that arrived before delivery is already
        # part of the first card, so it is not forwarded twice.
        await leads.send_update(attached.lead_id, photo_file_id=photo.file_id)


async def image_document(message: Message) -> None:
    # Files keep EXIF (incl. geolocation); a compressed photo is re-encoded by Telegram.
    await message.answer(texts.SEND_AS_PHOTO)


def create_router() -> Router:
    router = Router(name="media")
    router.message.register(voice_message, F.voice)
    router.message.register(photo_message, F.photo)
    router.message.register(image_document, F.document.mime_type.startswith("image/"))
    return router
