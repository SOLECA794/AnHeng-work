# 安全巡检报告

- **目标**: http://127.0.0.1:8080 (DVWA)
- **扫描时间**: 2026-10-15 21:39:29
- **技术栈识别**: , 
- **Nuclei 命中**: 15 条

## 风险分级统计


## 风险详情

### 1. [INFO] Cookies without HttpOnly attribute - Detect
- 位置: `host.docker.internal:8080`

### 2. [INFO] Gitignore Config - Detect
- 位置: `http://host.docker.internal:8080/.gitignore`

### 3. [INFO] HTTP Missing Security Headers
- 位置: `http://host.docker.internal:8080/login.php`

### 4. [INFO] HTTP Missing Security Headers
- 位置: `http://host.docker.internal:8080/login.php`

### 5. [INFO] HTTP Missing Security Headers
- 位置: `http://host.docker.internal:8080/login.php`

### 6. [INFO] HTTP Missing Security Headers
- 位置: `http://host.docker.internal:8080/login.php`

### 7. [INFO] HTTP Missing Security Headers
- 位置: `http://host.docker.internal:8080/login.php`

### 8. [INFO] HTTP Missing Security Headers
- 位置: `http://host.docker.internal:8080/login.php`

### 9. [INFO] HTTP Missing Security Headers
- 位置: `http://host.docker.internal:8080/login.php`

### 10. [INFO] HTTP Missing Security Headers
- 位置: `http://host.docker.internal:8080/login.php`

### 11. [INFO] HTTP Missing Security Headers
- 位置: `http://host.docker.internal:8080/login.php`

### 12. [INFO] HTTP Missing Security Headers
- 位置: `http://host.docker.internal:8080/login.php`

### 13. [INFO] Missing Cookie SameSite Strict
- 位置: `http://host.docker.internal:8080`

### 14. [MEDIUM] Sensitive Configuration Files Listing - Detect
- 位置: `http://host.docker.internal:8080/config/`

### 15. [INFO] README.md file disclosure
- 位置: `http://host.docker.internal:8080/README.md`
