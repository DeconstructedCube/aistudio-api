from pathlib import Path

from aistudio_api.domain.models import parse_image_output


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_parse_image_output_keeps_only_final_images_in_images_field():
    raw = (FIXTURES / "test_image_output.json").read_text()
    output = parse_image_output(raw)

    assert len(output.images) == 1
    assert len(output.reasoning_images) == 1
    assert output.images[0].mime == "image/jpeg"
    assert output.reasoning_images[0].mime == "image/jpeg"
    assert output.images[0].data != output.reasoning_images[0].data
    assert output.thinking.startswith("**Envisioning a Kitty Scene**")
