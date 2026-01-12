"""
Tests for language code module.

Tests cover:
- LanguageCode enum values and properties
- Language code conversion methods
- Azure locale mapping
- Language parsing from various formats
"""

import pytest


class TestLanguageCodeEnum:
    """Test LanguageCode enum basic functionality."""
    
    def test_english_properties(self):
        """Test English language code properties."""
        from app.utils.language_code import LanguageCode
        
        lang = LanguageCode.ENGLISH
        assert lang.iso_639_1 == "en"
        assert lang.iso_639_2_t == "eng"
        assert lang.iso_639_2_b == "eng"
        assert lang.name_en == "English"
        assert lang.name_native == "English"
    
    def test_german_properties(self):
        """Test German language code properties."""
        from app.utils.language_code import LanguageCode
        
        lang = LanguageCode.GERMAN
        assert lang.iso_639_1 == "de"
        assert lang.iso_639_2_t == "deu"
        assert lang.iso_639_2_b == "ger"
        assert lang.name_en == "German"
        assert lang.name_native == "Deutsch"
    
    def test_french_properties(self):
        """Test French language code properties."""
        from app.utils.language_code import LanguageCode
        
        lang = LanguageCode.FRENCH
        assert lang.iso_639_1 == "fr"
        assert lang.iso_639_2_t == "fra"
        assert lang.iso_639_2_b == "fre"
        assert lang.name_en == "French"
        assert lang.name_native == "Français"
    
    def test_none_language(self):
        """Test NONE language code."""
        from app.utils.language_code import LanguageCode
        
        lang = LanguageCode.NONE
        assert lang.iso_639_1 is None
        assert lang.iso_639_2_t is None
        assert lang.iso_639_2_b is None


class TestFromIso6391:
    """Test from_iso_639_1 method."""
    
    def test_valid_codes(self):
        """Test parsing valid ISO 639-1 codes."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.from_iso_639_1("en") == LanguageCode.ENGLISH
        assert LanguageCode.from_iso_639_1("de") == LanguageCode.GERMAN
        assert LanguageCode.from_iso_639_1("fr") == LanguageCode.FRENCH
        assert LanguageCode.from_iso_639_1("es") == LanguageCode.SPANISH
        assert LanguageCode.from_iso_639_1("ja") == LanguageCode.JAPANESE
        assert LanguageCode.from_iso_639_1("zh") == LanguageCode.CHINESE
    
    def test_invalid_code_returns_none(self):
        """Test that invalid codes return NONE."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.from_iso_639_1("xx") == LanguageCode.NONE
        assert LanguageCode.from_iso_639_1("invalid") == LanguageCode.NONE


class TestFromIso6392:
    """Test from_iso_639_2 method."""
    
    def test_valid_terminology_codes(self):
        """Test parsing valid ISO 639-2/T codes."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.from_iso_639_2("eng") == LanguageCode.ENGLISH
        assert LanguageCode.from_iso_639_2("deu") == LanguageCode.GERMAN
        assert LanguageCode.from_iso_639_2("fra") == LanguageCode.FRENCH
    
    def test_valid_bibliographic_codes(self):
        """Test parsing valid ISO 639-2/B codes."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.from_iso_639_2("ger") == LanguageCode.GERMAN
        assert LanguageCode.from_iso_639_2("fre") == LanguageCode.FRENCH
    
    def test_invalid_code_returns_none(self):
        """Test that invalid codes return NONE."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.from_iso_639_2("xxx") == LanguageCode.NONE


class TestFromName:
    """Test from_name method."""
    
    def test_english_names(self):
        """Test parsing English language names."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.from_name("English") == LanguageCode.ENGLISH
        assert LanguageCode.from_name("German") == LanguageCode.GERMAN
        assert LanguageCode.from_name("French") == LanguageCode.FRENCH
        assert LanguageCode.from_name("Spanish") == LanguageCode.SPANISH
    
    def test_native_names(self):
        """Test parsing native language names."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.from_name("Deutsch") == LanguageCode.GERMAN
        assert LanguageCode.from_name("Français") == LanguageCode.FRENCH
        assert LanguageCode.from_name("Español") == LanguageCode.SPANISH
    
    def test_case_insensitive(self):
        """Test case-insensitive name matching."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.from_name("english") == LanguageCode.ENGLISH
        assert LanguageCode.from_name("ENGLISH") == LanguageCode.ENGLISH
        assert LanguageCode.from_name("EnGlIsH") == LanguageCode.ENGLISH


class TestFromString:
    """Test from_string universal parser."""
    
    def test_iso_639_1_codes(self):
        """Test parsing ISO 639-1 codes."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.from_string("en") == LanguageCode.ENGLISH
        assert LanguageCode.from_string("de") == LanguageCode.GERMAN
        assert LanguageCode.from_string("ja") == LanguageCode.JAPANESE
    
    def test_iso_639_2_codes(self):
        """Test parsing ISO 639-2 codes."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.from_string("eng") == LanguageCode.ENGLISH
        assert LanguageCode.from_string("ger") == LanguageCode.GERMAN
        assert LanguageCode.from_string("deu") == LanguageCode.GERMAN
    
    def test_language_names(self):
        """Test parsing language names."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.from_string("English") == LanguageCode.ENGLISH
        assert LanguageCode.from_string("German") == LanguageCode.GERMAN
        assert LanguageCode.from_string("Deutsch") == LanguageCode.GERMAN
    
    def test_with_whitespace(self):
        """Test handling of whitespace."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.from_string("  en  ") == LanguageCode.ENGLISH
        assert LanguageCode.from_string("\tde\n") == LanguageCode.GERMAN
    
    def test_none_input(self):
        """Test handling of None input."""
        from app.utils.language_code import LanguageCode

        # Note: from_string accepts str, but handles None internally
        # This tests the actual behavior even if type hints say otherwise
        result = LanguageCode.from_string(None)  # type: ignore
        assert result == LanguageCode.NONE
    
    def test_invalid_returns_none(self):
        """Test that invalid strings return NONE."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.from_string("xyz") == LanguageCode.NONE
        assert LanguageCode.from_string("invalid") == LanguageCode.NONE
        assert LanguageCode.from_string("") == LanguageCode.NONE


class TestIsValidLanguage:
    """Test is_valid_language static method."""
    
    def test_valid_languages(self):
        """Test that valid languages return True."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.is_valid_language("en") is True
        assert LanguageCode.is_valid_language("eng") is True
        assert LanguageCode.is_valid_language("English") is True
        assert LanguageCode.is_valid_language("de") is True
    
    def test_invalid_languages(self):
        """Test that invalid languages return False."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.is_valid_language("xyz") is False
        assert LanguageCode.is_valid_language("invalid") is False
        assert LanguageCode.is_valid_language("") is False


class TestToAzureLocale:
    """Test Azure locale conversion."""
    
    def test_common_locales(self):
        """Test common Azure locale mappings."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.ENGLISH.to_azure_locale() == "en-US"
        assert LanguageCode.GERMAN.to_azure_locale() == "de-DE"
        assert LanguageCode.FRENCH.to_azure_locale() == "fr-FR"
        assert LanguageCode.SPANISH.to_azure_locale() == "es-ES"
        assert LanguageCode.JAPANESE.to_azure_locale() == "ja-JP"
        assert LanguageCode.CHINESE.to_azure_locale() == "zh-CN"
    
    def test_european_locales(self):
        """Test European language locales."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.ITALIAN.to_azure_locale() == "it-IT"
        assert LanguageCode.DUTCH.to_azure_locale() == "nl-NL"
        assert LanguageCode.POLISH.to_azure_locale() == "pl-PL"
        assert LanguageCode.SWEDISH.to_azure_locale() == "sv-SE"


class TestToIsoMethods:
    """Test ISO code conversion methods."""
    
    def test_to_iso_639_1(self):
        """Test to_iso_639_1 method."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.ENGLISH.to_iso_639_1() == "en"
        assert LanguageCode.GERMAN.to_iso_639_1() == "de"
        assert LanguageCode.FRENCH.to_iso_639_1() == "fr"
    
    def test_to_iso_639_2_t(self):
        """Test to_iso_639_2_t method (terminology)."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.ENGLISH.to_iso_639_2_t() == "eng"
        assert LanguageCode.GERMAN.to_iso_639_2_t() == "deu"
        assert LanguageCode.FRENCH.to_iso_639_2_t() == "fra"
    
    def test_to_iso_639_2_b(self):
        """Test to_iso_639_2_b method (bibliographic)."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.ENGLISH.to_iso_639_2_b() == "eng"
        assert LanguageCode.GERMAN.to_iso_639_2_b() == "ger"  # Different from T
        assert LanguageCode.FRENCH.to_iso_639_2_b() == "fre"  # Different from T


class TestToName:
    """Test to_name method."""
    
    def test_english_names(self):
        """Test getting English names."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.ENGLISH.to_name(in_english=True) == "English"
        assert LanguageCode.GERMAN.to_name(in_english=True) == "German"
        assert LanguageCode.FRENCH.to_name(in_english=True) == "French"
    
    def test_native_names(self):
        """Test getting native names."""
        from app.utils.language_code import LanguageCode
        
        assert LanguageCode.ENGLISH.to_name(in_english=False) == "English"
        assert LanguageCode.GERMAN.to_name(in_english=False) == "Deutsch"
        assert LanguageCode.FRENCH.to_name(in_english=False) == "Français"


class TestLanguageCodeCompleteness:
    """Test completeness of language code definitions."""
    
    def test_all_common_languages_exist(self):
        """Test that all commonly used languages are defined."""
        from app.utils.language_code import LanguageCode

        # Major world languages
        common_languages = [
            "ENGLISH", "SPANISH", "FRENCH", "GERMAN", "ITALIAN",
            "PORTUGUESE", "RUSSIAN", "CHINESE", "JAPANESE", "KOREAN",
            "ARABIC", "HINDI", "DUTCH", "POLISH", "TURKISH",
            "SWEDISH", "NORWEGIAN", "DANISH", "FINNISH", "GREEK"
        ]
        
        for lang_name in common_languages:
            assert hasattr(LanguageCode, lang_name), f"Missing language: {lang_name}"
    
    def test_no_duplicate_iso_639_1_codes(self):
        """Test that ISO 639-1 codes are unique."""
        from app.utils.language_code import LanguageCode
        
        seen_codes = {}
        for lang in LanguageCode:
            if lang == LanguageCode.NONE or lang.iso_639_1 is None:
                continue
            code = lang.iso_639_1
            assert code not in seen_codes, f"Duplicate ISO 639-1 code: {code}"
            seen_codes[code] = lang


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
