from app.db.models import CatalogCategory

# Image 1 is the client's room photo, image 2 is the catalog reference (see TryOnService).
# Title and style come from our own catalog.yaml, never from the user.
# The model tends to redraw the reference scene (its windows, floor, lights, camera angle)
# instead of editing the client's photo: the prompts forbid that explicitly.

PRESERVE_ROOM = """\
Edit the FIRST image; do not create a new scene. The result must be the same photo of the \
same room: identical camera position, angle, field of view, framing and aspect ratio — no \
zooming out, cropping or extending the room beyond the frame. Keep every architectural \
element exactly as it is and where it is: walls and their layout, corners, ceiling and its \
finish, exposed pipes and wiring, sockets, wall openings, doors and windows. The floor \
keeps its own covering, color and pattern from the first image (tiles stay the same \
tiles); never replace it with the floor of the second image. Do NOT add anything that is \
not in the first image: no new windows, doors, niches, beams, ceiling lights, lamps, \
plants, decor, people or text. If the photo shows only a corner or part of the room, work \
only with the visible walls. You may remove loose construction \
debris, tools, bags, cables and protective cardboard or boards lying on the floor; show the \
room's own floor covering in their place.\
"""

FURNITURE_PROMPT = """\
The first image is a photo of a client's room. The second image is a catalog example \
of custom furniture: {title} ({style}).
Install furniture of this design into the room from the first image, along its visible \
walls, at the real scale of the room, so that it looks like a real photo of the finished \
installation. Take ONLY the furniture design from the second image: facade color and \
finish, materials, countertop, handles and proportions. Ignore everything else in the \
second image — its room, windows, walls, floor, ceiling, lighting, plants, decor and \
camera angle.
{preserve}\
"""

INTERIOR_PROMPT = """\
The first image is a photo of a client's room. The second image is a reference interior: \
{title} ({style}).
Furnish the room from the first image in the style of the reference: furniture, colors, \
materials, textiles and decor, so that it looks like a real photo of the finished room. \
Take ONLY the style from the second image; do not copy its room, windows, layout or \
camera angle.
{preserve}\
"""


def tryon_prompt(category: CatalogCategory, title: str, style: str | None) -> str:
    template = INTERIOR_PROMPT if category is CatalogCategory.INTERIOR else FURNITURE_PROMPT
    return template.format(title=title, style=style or title, preserve=PRESERVE_ROOM)
