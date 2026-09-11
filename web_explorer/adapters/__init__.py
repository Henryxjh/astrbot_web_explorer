from .generic import GenericAdapter
from .github import GitHubAdapter
from .mcmod import McmodAdapter
from .mediawiki import MediaWikiAdapter
from .readthedocs import ReadTheDocsAdapter

__all__ = [
    "GenericAdapter",
    "GitHubAdapter",
    "McmodAdapter",
    "MediaWikiAdapter",
    "ReadTheDocsAdapter",
]
