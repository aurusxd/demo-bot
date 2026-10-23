from aiogram.fsm.state import State, StatesGroup


class TryOn(StatesGroup):
    # data: {"item_id": int} — the catalog item to put into the room photo.
    waiting_photo = State()
