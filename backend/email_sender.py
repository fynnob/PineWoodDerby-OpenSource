"""
Pinewood Derby — Local SMTP Email Sender
Mirrors the Supabase edge function but runs locally.
"""
import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText


async def send_registration_email(config: dict, payload: dict) -> tuple[bool, str]:
    """
    Send a registration confirmation email with the check-in QR code.
    payload: { to, device_token, short_code, reg_url }
    Returns (ok: bool, message: str)
    """
    if not config.get("email_enabled"):
        return False, "Email not enabled in config"

    to           = payload.get("to", "")
    device_token = payload.get("device_token", "")
    short_code   = payload.get("short_code", "????")
    reg_url      = payload.get("reg_url", "")

    if not to or "@" not in to:
        return False, "Invalid recipient address"

    smtp_host = config.get("smtp_host", "smtp.zoho.eu")
    smtp_port = int(config.get("smtp_port", 587))
    smtp_user = config.get("smtp_user", "")
    smtp_pass = config.get("smtp_password", "")
    from_name = config.get("smtp_from_name", "Pinewood Derby")
    from_addr = config.get("smtp_from_address", smtp_user)
    track     = config.get("track_name", "Pinewood Derby")

    qr_url = (
        f"https://api.qrserver.com/v1/create-qr-code/"
        f"?size=280x280&data={device_token}&bgcolor=ffffff&color=1a1e2e&margin=14"
    )

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/></head>
<body style="margin:0;padding:0;background:#f0f2f8;font-family:'Helvetica Neue',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f0f2f8;padding:32px 16px;">
<tr><td align="center">
<table width="540" cellpadding="0" cellspacing="0" border="0"
       style="max-width:540px;width:100%;background:#fff;border-radius:18px;overflow:hidden;
              box-shadow:0 6px 32px rgba(0,0,0,.12);">
  <tr><td style="background:#1a1e2e;padding:32px;text-align:center;">
    <div style="font-size:2.4rem;">🏎️</div>
    <h1 style="margin:10px 0 4px;color:#f59e0b;font-size:1.6rem;font-weight:900;
               letter-spacing:2px;text-transform:uppercase;">{track}</h1>
    <p style="color:#6b738f;margin:0;font-size:0.8rem;letter-spacing:3px;text-transform:uppercase;">
      You're Registered!</p>
  </td></tr>
  <tr><td style="padding:32px 32px 0;">
    <h2 style="margin:0 0 10px;color:#1a1e2e;font-size:1.2rem;font-weight:800;">Welcome to the race! 🎉</h2>
    <p style="margin:0 0 24px;color:#4b5563;line-height:1.7;font-size:0.95rem;">
      Your car(s) have been pre-registered. Show the QR code below at <strong>check-in</strong>.
    </p>
  </td></tr>
  <tr><td style="padding:0 32px;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr><td style="background:#f8f9fc;border-radius:14px;padding:24px;text-align:center;
                     border:2px solid #e5e7eb;">
        <div style="font-size:0.65rem;font-weight:700;letter-spacing:5px;text-transform:uppercase;
                    color:#9ca3af;margin-bottom:8px;">Your Check-in Code</div>
        <div style="font-size:3rem;font-weight:900;color:#1a1e2e;letter-spacing:12px;line-height:1;">
          {short_code}</div>
      </td></tr>
    </table>
  </td></tr>
  <tr><td style="padding:24px 32px 8px;text-align:center;">
    <img src="{qr_url}" width="200" height="200" alt="QR Code"
         style="display:block;border-radius:12px;border:2px solid #e5e7eb;margin:0 auto;" />
  </td></tr>
  <tr><td style="padding:28px 32px;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr><td align="center">
        <a href="{reg_url}" style="display:inline-block;background:#f59e0b;color:#1a1e2e;
           text-decoration:none;padding:15px 36px;border-radius:10px;font-weight:800;">
          📱 View Your Registration</a>
      </td></tr>
    </table>
  </td></tr>
  <tr><td style="background:#f8f9fc;padding:20px 32px;text-align:center;
                 border-top:1px solid #e5e7eb;">
    <p style="margin:0;color:#9ca3af;font-size:0.75rem;line-height:1.6;">
      Sent automatically by {track}. You will only receive this email once per device.
    </p>
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Your {track} Check-in QR Code 🏁"
    msg["From"]    = f'"{from_name}" <{from_addr}>'
    msg["To"]      = to
    msg.attach(MIMEText(html, "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=smtp_port,
            username=smtp_user,
            password=smtp_pass,
            start_tls=True,
        )
        return True, "Email sent"
    except Exception as e:
        return False, str(e)
