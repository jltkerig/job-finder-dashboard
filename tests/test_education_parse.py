import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_database  # noqa: F401  (cuts tests off from the real database)
from jobfinder.profiles.education_parse import parse_education
from jobfinder.web.schema import EDUCATION_DEGREES

FIELDS = {"school", "degree", "major", "minor", "start_date", "end_date", "gpa"}


class ParseEducation(unittest.TestCase):
    def test_schools_with_a_place_and_a_degree_line(self):
        text = ("Work Experience\nDesigner\nAcme\n2020 - 2022\nEducation History\nSpringfield University\nSpringfield, MD\n"
                "Bachelor of Science, Graphic Design\nRiverside College\nRiverside, MD\nAssociate Degree, Graphic Design\n"
                "Additional Experience\nVolunteer for Word on the Street\n")
        schools = parse_education(text)
        self.assertEqual([(s["school"], s["degree"], s["major"]) for s in schools],
                         [("Springfield University", "Bachelor's Degree", "Graphic Design"),
                          ("Riverside College", "Associate's Degree", "Graphic Design")])
        self.assertTrue(all(set(s) == FIELDS and s["degree"] in EDUCATION_DEGREES for s in schools))

    def test_the_degree_may_come_before_the_school(self):
        text = "EDUCATION\nB.S. in Computer Science\nUniversity of Maryland\nMay 2012\n"
        (school,) = parse_education(text)
        self.assertEqual((school["school"], school["degree"], school["major"], school["end_date"]),
                         ("University of Maryland", "Bachelor's Degree", "Computer Science", "2012-05"))

    def test_gpa_minor_and_a_month_range_are_read(self):
        text = ("Education\nSpringfield University\nMaster of Arts, Design\nGPA: 3.8\nMinor in Marketing\n"
                "August 2008 - May 2012\nSkills\nPhotoshop\n")
        (school,) = parse_education(text)
        self.assertEqual((school["gpa"], school["minor"], school["start_date"], school["end_date"], school["degree"]),
                         ("3.8", "Marketing", "2008-08", "2012-05", "Master's Degree"))

    def test_a_second_degree_at_the_same_school_is_its_own_entry(self):
        text = "Education\nSpringfield University\nBachelor of Arts, Art\nMaster of Arts, Design\n"
        self.assertEqual([(s["school"], s["degree"]) for s in parse_education(text)],
                         [("Springfield University", "Bachelor's Degree"), ("Springfield University", "Master's Degree")])

    def test_a_high_school_is_a_school_and_its_diploma_a_degree(self):
        text = "Education\nPerryville High School\nHigh School Diploma\n"
        (school,) = parse_education(text)
        self.assertEqual((school["school"], school["degree"]), ("Perryville High School", "High School Diploma"))

    def test_degree_first_entries_keep_each_degree_with_the_school_listed_after_it(self):
        text = ("Education\nB.S. in Computer Science\nUniversity of Maryland\nM.S. in Data Science\n"
                "Johns Hopkins University\n")
        self.assertEqual([(s["school"], s["degree"], s["major"]) for s in parse_education(text)],
                         [("University of Maryland", "Bachelor's Degree", "Computer Science"),
                          ("Johns Hopkins University", "Master's Degree", "Data Science")])

    def test_a_heading_after_education_ends_the_section(self):
        text = "Education\nSpringfield University\nBachelor of Arts, Art\nHonors & Awards\nDean's List, Springfield School\n"
        self.assertEqual([s["school"] for s in parse_education(text)], ["Springfield University"])

    def test_no_education_heading_means_nothing(self):
        self.assertEqual(parse_education("Work Experience\nDesigner\nAcme\n2020 - 2022\nSkills\nPhotoshop\n"), [])


if __name__ == "__main__":
    unittest.main()
