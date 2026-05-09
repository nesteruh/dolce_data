import os
from typing import List, Set

def search_files(
    root_dir: str,
    query: str,
    max_results: int = 10,
    match_full_path: bool = False,
    follow_symlinks: bool = False,
) -> List[str]:
    """
    Recursively search for files under `root_dir` whose filename or full path contains `query`
    (case-insensitive). Returns up to `max_results` absolute file paths.

    Args:
        root_dir: Path to the directory where the search starts.
        query: Substring to search for (case-insensitive) in filenames or full paths.
        max_results: Maximum number of results to return (default 10).
        match_full_path: If True, match `query` against the full absolute path; otherwise match only the filename.
        follow_symlinks: If True, follow directory symlinks while walking.

    Returns:
        List[str]: A list of absolute file paths (up to `max_results`) matching the query.
    """
    query_lower = query.lower()
    matches: List[str] = []
    seen: Set[str] = set()

    for dirpath, dirnames, filenames in os.walk(root_dir, followlinks=follow_symlinks, onerror=lambda e: None):
        for fn in filenames:
            candidate = os.path.abspath(os.path.join(dirpath, fn))
            if candidate in seen:
                continue
            hay = candidate.lower() if match_full_path else fn.lower()
            if query_lower in hay:
                matches.append(candidate)
                seen.add(candidate)
                if len(matches) >= max_results:
                    return matches
    return matches

if __name__ == "__main__":
    # for i in search_files(root_dir="D:\me\code\dolce_data", query="gitignore"):
    #     print(i)
    for i in search_files(root_dir=r"D:\\", query="A", max_results=1000):
        print(i)
