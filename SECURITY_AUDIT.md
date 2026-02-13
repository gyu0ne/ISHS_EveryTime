# ISHS EveryTime Security Audit Report

## Executive Summary
A comprehensive security audit was conducted on the "ISHS EveryTime" Flask application. The review focused on `app.py`, `route/RiroSchoolAuth.py`, and HTML templates. Several critical vulnerabilities were identified, including an authentication bypass, stored Cross-Site Scripting (XSS), and a missing function definition causing server errors.

## Findings Summary

| Severity | Count | Vulnerability |
| :--- | :--- | :--- |
| 🔴 Critical | 3 | Authentication Bypass, Stored XSS, Missing Function Definition |
| zk Medium | 2 | CSRF in Guest Delete, Race Condition in Shop |
| 🔵 Low | 1 | Potential IDOR in Reaction Logic (Mitigated) |

## Detailed Findings

### 🔴 Critical/High Severity

#### 1. Authentication Bypass (Hardcoded Credentials)
*   **File:** `app.py` (Line ~530, `riro_auth` function)
*   **Issue Description:** The authentication logic contains debug code that hardcodes the user session to a specific identity ('김준서', '1310') regardless of the actual user logging in. The real authentication response from `RiroSchoolAuth` is ignored.
*   **Exploit Scenario:** Any user who can pass the initial Riro login check (or if the check is weak) will be logged in as the hardcoded user. If the Riro login check is bypassed or if multiple users use the system, they will all share the same identity, leading to complete account takeover or identity spoofing.
*   **Suggested Fix:** Remove the hardcoded session variables and uncomment the logic that uses `api_result`.

#### 2. Stored XSS via `iframe` `data:` Protocol
*   **File:** `app.py` (`post_write`, `post_edit`, `post_write_guest`)
*   **Issue Description:** The `bleach` configuration allows `iframe` tags and the `data` protocol in `src` attributes. This allows attackers to inject malicious JavaScript via `<iframe src="data:text/html;base64,...">`.
*   **Exploit Scenario:** An attacker posts a message containing a malicious iframe. When other users view the post, the JavaScript executes in their browser context, potentially stealing cookies or performing actions on their behalf.
*   **Suggested Fix:** Remove `data` from the allowed protocols list in `bleach.clean`.

#### 3. Server Error due to Missing Function `clean_fts_query`
*   **File:** `app.py` (`search` route)
*   **Issue Description:** The `search` function calls `clean_fts_query(query)`, but this function is not defined anywhere in the application.
*   **Exploit Scenario:** Any attempt to use the search feature will result in a 500 Internal Server Error (`NameError`).
*   **Suggested Fix:** Define the `clean_fts_query` function to sanitize search inputs.

### zk Medium Severity

#### 4. CSRF in Guest Delete Routes
*   **File:** `app.py` (`post_delete_guest`, `comment_delete_guest`)
*   **Issue Description:** These routes use the `GET` method to perform state-changing actions (deletion). While they check for a session token (`guest_auth_post_{id}`), if an attacker can trick a user (who has just authenticated as guest) into visiting a link, the post could be deleted.
*   **Exploit Scenario:** A user enters the guest password to edit a post. They now have the session token. An attacker sends them a link `<img src="/post-delete-guest/123">`. The browser requests the URL, and the post is deleted without confirmation.
*   **Suggested Fix:** Change these routes to `POST` or handle the deletion logic directly within the `guest_auth` POST handler.

#### 5. Race Condition in Etacon Shop
*   **File:** `app.py` (`buy_etacon` function)
*   **Issue Description:** The application checks if a user has enough points and then updates the point balance in a separate SQL statement.
*   **Exploit Scenario:** A user with sufficient points for one item sends multiple purchase requests simultaneously. All requests pass the check before the balance is updated, allowing the user to buy multiple items with the same points or drive their balance negative.
*   **Suggested Fix:** Use a single atomic SQL `UPDATE` statement that includes the condition `WHERE point >= price` and check `cursor.rowcount`.

### 🔵 Low/Informational

#### 6. Potential Logic Flaws
*   **Issue:** `update_exp_level` logic for negative experience points (leveling down) works but relies on implicit integer division behavior.
*   **Issue:** `process_etacons` constructs HTML manually. While currently safe due to regex constraints, it's brittle.
