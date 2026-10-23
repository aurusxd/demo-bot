from app.db.models import CatalogCategory

# Image 1 is the client's room photo, image 2 is the catalog reference (see TryOnService).
# The title comes from our own catalog.yaml, never from the user.

FURNITURE_PROMPT = """\
The first image is a photo of a client's room. The second image is a catalog example \
of custom furniture: {title}.
Place furniture of this design into the room from the first image so that it looks \
like a real photo of the finished installation. Fit it to a suitable wall and to the real \
scale of the room. Keep the room itself unchanged: walls, windows, doors, floor, ceiling, \
perspective, camera angle and lighting. Reproduce the furniture's style, colors, materials \
and handles from the second image. Do not add text, logos or watermarks.\
"""

INTERIOR_PROMPT = """\
The first image is a photo of a client's room. The second image is a reference interior: \
{title}.
Redesign the room from the first image in the style of the reference: furniture, colors, \
materials, textiles and decor, so that it looks like a real photo of the finished renovation. \
Keep the room's architecture unchanged: walls, windows, doors, ceiling height, perspective, \
camera angle and natural lighting. Do not add text, logos or watermarks.\
"""


def tryon_prompt(category: CatalogCategory, title: str) -> str:
    template = INTERIOR_PROMPT if category is CatalogCategory.INTERIOR else FURNITURE_PROMPT
    return template.format(title=title)
