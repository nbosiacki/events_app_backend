# Authentication Module - Security Documentation

This document explains the security decisions and components used in the authentication system. Written for developers who want to understand *why* these choices were made, not just *what* they do.

---

## Table of Contents
1. [Password Hashing: Why Argon2id?](#password-hashing-why-argon2id)
2. [JWT Tokens: Access vs Refresh](#jwt-tokens-access-vs-refresh)
3. [Token Storage: Why Memory + localStorage?](#token-storage-why-memory--localstorage)
4. [Libraries Used](#libraries-used)
5. [Security Measures Explained](#security-measures-explained)
6. [Common Attack Mitigations](#common-attack-mitigations)

---

## Password Hashing: Why Argon2id?

### The Problem
When storing passwords, you never store them in plain text. If your database is breached, attackers shouldn't be able to read passwords directly. Instead, you store a "hash" - a one-way mathematical transformation of the password.

### Why Not Just SHA-256 or MD5?
These are *fast* hashing algorithms, which is actually bad for passwords. An attacker with a GPU can compute billions of SHA-256 hashes per second, making brute-force attacks trivial.

### Password Hashing Algorithms (Historical Context)
1. **MD5/SHA-1** - Broken. Never use for passwords.
2. **bcrypt** (1999) - Good, but showing age. Fixed memory usage makes it vulnerable to specialized hardware.
3. **scrypt** (2009) - Added memory-hardness. Good, but tricky to configure correctly.
4. **Argon2** (2015) - Winner of the Password Hashing Competition. Current gold standard.

### Why Argon2id Specifically?
Argon2 has three variants:
- **Argon2d** - Resistant to GPU attacks, but vulnerable to side-channel attacks
- **Argon2i** - Resistant to side-channel attacks, but less resistant to GPU attacks
- **Argon2id** - Hybrid of both. Best of both worlds. **This is what we use.**

### Our Configuration
```python
argon2__memory_cost=65536   # 64 MB of RAM required per hash
argon2__time_cost=3         # 3 iterations
argon2__parallelism=4       # 4 parallel threads
```

This means:
- Each password hash requires 64 MB of RAM, making GPU attacks expensive
- Takes ~0.5-1 second to compute (slow enough to prevent brute force, fast enough for UX)
- OWASP recommends these exact parameters

---

## JWT Tokens: Access vs Refresh

### What is a JWT?
A JSON Web Token is a signed piece of JSON data. The signature proves the token came from our server and hasn't been tampered with. Structure: `header.payload.signature`

Example payload:
```json
{
  "sub": "user_id_123",      // Subject (who this token is for)
  "exp": 1706400000,         // Expiration timestamp
  "iat": 1706398200,         // Issued at timestamp
  "type": "access",          // Token type
  "jti": "unique_token_id"   // JWT ID (for revocation)
}
```

### Why Two Token Types?

**Access Token (short-lived: 30 minutes)**
- Used for every API request
- If stolen, attacker has limited window
- Stored in memory only (not localStorage)

**Refresh Token (long-lived: 7 days)**
- Only used to get new access tokens
- Stored in localStorage (survives page refresh)
- Rotated on each use (old one invalidated)

### The Flow
1. User logs in → gets access token + refresh token
2. Access token used for API calls
3. Access token expires (30 min)
4. Frontend automatically uses refresh token to get new access token
5. User stays logged in without re-entering password

### Why Not Just One Long-Lived Token?
If a single token is stolen (XSS attack, network sniffing):
- **Long-lived token**: Attacker has access for days/weeks
- **Short-lived + refresh**: Attacker has 30 minutes max (access token), and refresh tokens are rotated

---

## Token Storage: Why Memory + localStorage?

### The Tradeoffs

| Storage | XSS Vulnerable? | Survives Refresh? | CSRF Vulnerable? |
|---------|-----------------|-------------------|------------------|
| localStorage | Yes | Yes | No |
| sessionStorage | Yes | No | No |
| Memory (JS variable) | Yes* | No | No |
| HttpOnly Cookie | No | Yes | Yes |

*Memory is "vulnerable" to XSS but harder to exfiltrate than localStorage

### Our Strategy
- **Access token in memory**: If page is XSS'd, token is harder to steal (no `localStorage.getItem`). Lost on refresh, but that's fine - we have refresh tokens.
- **Refresh token in localStorage**: Needs to survive page refresh. If XSS'd, attacker can get it, but refresh tokens are rotated on use, limiting damage.

### Why Not HttpOnly Cookies?
They prevent XSS token theft entirely, but:
1. Require CSRF protection (complexity)
2. Complicate CORS in development
3. Don't work well with some API architectures

For a MVP/placeholder auth system, JWT in headers is simpler and still secure if combined with other protections.

---

## Libraries Used

### `passlib[argon2]`
**What**: Python library for password hashing with multiple algorithm support.
**Why**: Handles Argon2 properly, includes constant-time comparison (prevents timing attacks), auto-upgrades hash parameters.

### `python-jose[cryptography]`
**What**: JWT encoding/decoding library.
**Why**: Well-maintained, supports multiple algorithms (HS256, RS256), backed by `cryptography` library for secure primitives.

### `python-multipart`
**What**: Parses `multipart/form-data` and `application/x-www-form-urlencoded`.
**Why**: Required by FastAPI for OAuth2PasswordRequestForm (standard login form format).

### `email-validator`
**What**: Validates email addresses.
**Why**: Checks syntax AND deliverability (MX records). Prevents fake emails like `user@localhost`.

---

## Security Measures Explained

### Account Lockout (5 attempts, 15 minutes)
**Why**: Prevents brute-force password guessing. After 5 failed attempts, account is locked for 15 minutes. Attacker can only try 5 passwords per 15 minutes = 20 passwords/hour = useless.

### Password Requirements (8+ chars, mixed case, digit)
**Why**: Forces minimum entropy. "password" is weak; "P4ssword!" is better. Not perfect (people still use "P4ssword!"), but raises the bar.

### JWT Unique ID (jti claim)
**Why**: Enables token revocation. If you need to invalidate all tokens (password change, security incident), you can blacklist by jti.

### Token Rotation
**Why**: Refresh tokens are single-use. When you use one to get new tokens, you get a NEW refresh token. If an attacker steals your refresh token and uses it, YOUR next request will fail (token already used), alerting you to compromise.

### Email Enumeration Prevention
**Why**: "Forgot password" always returns the same response whether email exists or not. Attackers can't use this to discover valid email addresses.

---

## Common Attack Mitigations

### Brute Force Attacks
**Attack**: Try millions of password combinations.
**Mitigation**: Account lockout + Argon2 slow hashing.

### Credential Stuffing
**Attack**: Use leaked username/password combos from other breaches.
**Mitigation**: Account lockout + password requirements (forces unique passwords).

### Timing Attacks
**Attack**: Measure response time to determine if username exists (longer = user found + password checked).
**Mitigation**: passlib uses constant-time comparison; we always check password hash even for non-existent users.

### XSS (Cross-Site Scripting)
**Attack**: Inject malicious JavaScript to steal tokens.
**Mitigation**: Access token in memory (harder to steal), short expiry, React auto-escapes output.

### CSRF (Cross-Site Request Forgery)
**Attack**: Trick user's browser into making requests to our API.
**Mitigation**: JWT in Authorization header (not cookies), so CSRF doesn't apply.

### Token Theft
**Attack**: Steal JWT through various means.
**Mitigation**: Short access token expiry (30 min), refresh token rotation, HTTPS in production.

---

## Future Improvements

1. **Rate limiting** - Add `slowapi` to limit requests per IP
2. **HttpOnly cookies** - Consider for production (better XSS protection)
3. **Password breach checking** - Check against HaveIBeenPwned API
4. **2FA/MFA** - Add TOTP support (Google Authenticator)
5. **OAuth 2.0** - Federated login (Google, Apple, Meta)

---

## References

- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- [Argon2 Paper](https://www.password-hashing.net/argon2-specs.pdf)
- [JWT Best Practices (RFC 8725)](https://datatracker.ietf.org/doc/html/rfc8725)
