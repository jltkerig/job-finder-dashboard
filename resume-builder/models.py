"""The shapes Claude fills in through the connector, and the web editor saves back."""
from typing import Literal

from pydantic import BaseModel, Field


class ResumeItem(BaseModel):
    heading: str = Field("", description="Job title, degree or project name.")
    subheading: str = Field("", description="Employer, school or organisation.")
    location: str = ""
    dates: str = Field("", description="As written on the resume, e.g. 'Jan 2021 – Present'.")
    bullets: list[str] = Field(default_factory=list)
    text: str = Field("", description="A short paragraph, used instead of or as well as bullets.")


class ResumeSection(BaseModel):
    title: str = Field(description="Section heading, e.g. 'Experience', 'Education', 'Skills'.")
    text: str = Field("", description="Paragraph content, e.g. a summary or a skills line.")
    items: list[ResumeItem] = Field(default_factory=list)


RELATIONSHIPS = ["Supervisor", "Manager", "CEO / Owner", "Co-worker", "Client", "Teacher / Professor", "Other"]


class Reference(BaseModel):
    id: str = ""
    relationship: str = Field("", description="How the user knows them, e.g. Supervisor, CEO / Owner, Co-worker.")
    name: str = ""
    job_title: str = Field("", description="The reference's own job title.")
    company: str = Field("", description="The reference's company.")
    user_job: str = Field("", description="The user's job when they worked together, e.g. 'Web Developer at Example Co'.")
    phone: str = ""
    email: str = ""
    notes: str = Field("", description="Private notes for the user, never printed.")


class PrintedReference(BaseModel):
    name: str
    relationship: str = ""
    job_title: str = ""
    company: str = ""
    user_job: str = Field("", description="The user's job when they worked together.")
    phone: str = ""
    email: str = ""


class ResumeContent(BaseModel):
    full_name: str
    headline: str = Field("", description="Optional line under the name, e.g. the target job title.")
    contact: list[str] = Field(default_factory=list, description="Email, phone, city/state, links - one per entry.")
    sections: list[ResumeSection] = Field(default_factory=list, description="In the order they should appear.")
    references: list[PrintedReference] = Field(
        default_factory=list,
        description="Only when the user asks for references: the ones they chose, printed as the last section.")


class CoverLetterContent(BaseModel):
    full_name: str
    contact: list[str] = Field(default_factory=list)
    date: str = Field("", description="Leave empty for today's date.")
    recipient: list[str] = Field(default_factory=list, description="Hiring manager, company, address - one line each.")
    greeting: str = "Dear Hiring Manager,"
    paragraphs: list[str] = Field(description="Body paragraphs, plain text.")
    closing: str = "Sincerely,"
    signature: str = Field("", description="Leave empty to use full_name.")


class LayoutSettings(BaseModel):
    """Overrides for the layout measured from the uploaded resume. Leave a field out to keep the measured value."""
    font_family: str | None = Field(None, description="e.g. 'Calibri', 'Georgia', 'Times New Roman'.")
    font_kind: Literal["serif", "sans"] | None = None
    body_size: float | None = Field(None, ge=7, le=14)
    name_size: float | None = Field(None, ge=10, le=40)
    heading_size: float | None = Field(None, ge=8, le=24)
    accent_color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    text_color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    name_align: Literal["left", "center"] | None = None
    heading_case: Literal["upper", "title"] | None = None
    heading_rule: bool | None = Field(None, description="Draw a line under each section heading.")
    margin_in: float | None = Field(None, ge=0.3, le=1.5)
    name_font: str | None = Field(None, max_length=60, description="Font for the name; empty means the body font.")
    heading_font: str | None = Field(None, max_length=60, description="Font for section headings; empty means the body font.")
    detail_font: str | None = Field(None, max_length=60, description="Font for dates, sub-headings and contact lines; empty means the body font.")
    margin_top: float | None = Field(None, ge=0.3, le=1.5, description="Empty means the general margin.")
    margin_bottom: float | None = Field(None, ge=0.3, le=1.5)
    margin_left: float | None = Field(None, ge=0.3, le=1.5)
    margin_right: float | None = Field(None, ge=0.3, le=1.5)
    line_spacing: float | None = Field(None, ge=1.0, le=1.8, description="Line height as a multiple of the text size.")
    section_gap: float | None = Field(None, ge=2, le=40, description="Space above each section heading, in points.")
    bullet_char: Literal["•", "–", "-", "·", "▪", "○", "›"] | None = None
    contact_separator: Literal["|", "•", "·", "/", ",", "–", "-"] | None = None
    page_size: Literal["letter", "a4"] | None = None
    header_rule: bool | None = Field(None, description="Draw a line under the name and contact lines.")


DEFAULT_LAYOUT = {
    "font_family": "Calibri", "font_kind": "sans", "body_size": 10.5, "name_size": 22,
    "heading_size": 12, "accent_color": "#1F3A5F", "text_color": "#222222", "name_align": "left",
    "heading_case": "upper", "heading_rule": True, "margin_in": 0.75,
    "name_font": "", "heading_font": "", "detail_font": "",
    "margin_top": None, "margin_bottom": None, "margin_left": None, "margin_right": None,
    "line_spacing": 1.25, "section_gap": None, "bullet_char": "•", "contact_separator": "|",
    "page_size": "letter", "header_rule": True,
}


def merge_layout(*layers):
    layout = dict(DEFAULT_LAYOUT)
    for layer in layers:
        if layer:
            values = layer.model_dump(exclude_none=True) if isinstance(layer, BaseModel) else layer
            layout.update({k: v for k, v in values.items() if k in DEFAULT_LAYOUT and v is not None})
    return LayoutSettings(**layout).model_dump()
