"""
Pattern Matcher

Responsibilities:
- Match templates
- Match using action + template key
- Provide safe fallback support
- Return dynamic template metadata
- Keep generation deterministic
"""

from typing import Any, Dict, Optional

from config.config_loader import get_config
from utils import setup_logger

logger = setup_logger(__name__)


class DynamicPatternMatcher:
    """
    Dynamic template matcher.
    """

    def __init__(
        self,
    ):

        self.config = get_config()

    def match_template(
        self,
        action: str,
        template_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Match dynamic template.

        Priority:
        1. template_key exact match
        2. action match
        3. fallback to JSON template
        """

        dynamic_templates = (
            self.config.get_runtime_cache(
                "dynamic_templates",
                {},
            )
        )

        if not dynamic_templates:

            logger.warning(
                "No dynamic templates found in runtime cache"
            )

            return None

        # Exact template_key match

        if template_key:

            key_matches = []

            for template_data in dynamic_templates.values():

                template_key_val = (
                    str(
                        template_data.get(
                            "template_key",
                            "",
                        )
                    )
                    .strip()
                    .lower()
                )

                if template_key_val == template_key.strip().lower():

                    key_matches.append(
                        template_data
                    )

            if key_matches:

                key_matches.sort(
                    key=lambda item: item.get(
                        "priority",
                        999,
                    )
                )

                logger.debug(
                    "Matched dynamic template by template_key | %s | variants=%s",
                    template_key,
                    len(key_matches),
                )

                return key_matches[0]
            
            # If template_key was specified but not found in DB, return None
            # to let the caller use the correct JSON fallback template.
            return None

        # Action-based fallback match

        action_matches = []

        normalized_action = (
            action.strip().lower()
        )

        for (
            key,
            template_data,
        ) in dynamic_templates.items():

            template_action = (
                str(
                    template_data.get(
                        "action",
                        "",
                    )
                )
                .strip()
                .lower()
            )

            if template_action == normalized_action:

                action_matches.append(
                    template_data
                )

        if action_matches:

            action_matches.sort(
                key=lambda item: item.get(
                    "priority",
                    999,
                )
            )

            logger.debug(
                "Matched dynamic template by action | %s",
                action,
            )

            return action_matches[0]

        logger.debug(
            "No dynamic template matched | action=%s | template_key=%s",
            action,
            template_key,
        )

        return None

    def get_template_string(
        self,
        action: str,
        template_key: Optional[str] = None,
    ) -> Optional[str]:
        """
        Get template string only.
        """

        matched_template = self.match_template(
            action=action,
            template_key=template_key,
        )

        if not matched_template:

            return None

        template = matched_template.get(
            "template",
            ""
        )

        if template:
            template = (
                template
                .replace("<<{", "{")
                .replace("}>>", "}")
                .replace("<<", "{")
                .replace(">>", "}")
            )

        return template

    def get_template_examples(
        self,
        action: str,
        template_key: Optional[str] = None,
    ) -> list:
        """
        Get dynamic examples.
        """

        matched_template = self.match_template(
            action=action,
            template_key=template_key,
        )

        if not matched_template:

            return []

        return matched_template.get(
            "examples",
            [],
        )

    def get_template_description(
        self,
        action: str,
        template_key: Optional[str] = None,
    ) -> str:
        """
        Get dynamic description.
        """

        matched_template = self.match_template(
            action=action,
            template_key=template_key,
        )

        if not matched_template:

            return ""

        return matched_template.get(
            "description",
            "",
        )

    def get_template_with_fallback(
        self,
        action: str,
        template_key: Optional[str] = None,
    ) -> Optional[str]:
        """
        Dynamic-safe template resolution.

        Resolution Order:
        1. Excel dynamic templates
        2. JSON fallback templates
        """

        dynamic_template = (
            self.get_template_string(
                action=action,
                template_key=template_key,
            )
        )

        if dynamic_template:

            return dynamic_template

        json_templates = (
            self.config.get_templates()
        )

        if (
            template_key
            and template_key in json_templates
        ):

            template = json_templates[template_key]

            if isinstance(template, list) and template:
                template = template[0]

            logger.warning(
                "Using JSON fallback template | %s",
                template_key,
            )

            return template

        logger.debug(
            "No template found | action=%s | template_key=%s",
            action,
            template_key,
        )

        return None


_pattern_matcher_instance = (
    DynamicPatternMatcher()
)


def get_pattern_matcher(
) -> DynamicPatternMatcher:
    """
    Get singleton matcher instance.
    """

    return _pattern_matcher_instance