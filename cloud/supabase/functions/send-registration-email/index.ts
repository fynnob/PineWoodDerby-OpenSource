// Pinewood Derby — Registration Email Edge Function
// Deploy: supabase functions deploy send-registration-email --no-verify-jwt
//
// Set this secret in Supabase Dashboard → Edge Functions → Secrets:
//   SMTP_PASSWORD = Fynnfynn2012

import nodemailer from "npm:nodemailer@6";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });

  try {
    console.log("[email] Function invoked, method:", req.method);

    const body = await req.json() as {
      to: string;
      device_token: string;
      short_code: string;
      reg_url: string;
    };
    const { to, device_token, short_code, reg_url } = body;
    console.log("[email] Payload — to:", to, "| short_code:", short_code, "| device_token length:", device_token?.length, "| reg_url:", reg_url);

    if (!to) throw new Error("Missing 'to' email address in payload");

    const smtpPass = Deno.env.get("SMTP_PASSWORD");
    console.log("[email] SMTP_PASSWORD present:", !!smtpPass);
    if (!smtpPass) throw new Error("SMTP_PASSWORD secret not set");

    console.log("[email] Creating SMTP transporter (smtp.zoho.eu:587)...");
    const transporter = nodemailer.createTransport({
      host: "smtp.zoho.eu",
      port: 587,
      secure: false, // STARTTLS
      auth: { user: "fynn@fynn.qzz.io", pass: smtpPass },
    });

    console.log("[email] Verifying SMTP connection...");
    await transporter.verify();
    console.log("[email] SMTP connection verified OK");

    const qrUrl =
      `https://api.qrserver.com/v1/create-qr-code/?size=280x280&data=${encodeURIComponent(device_token)}&bgcolor=ffffff&color=1a1e2e&margin=14`;

    console.log("[email] Sending email to:", to, "...");
    const info = await transporter.sendMail({
      from: '"Pinewood Derby 🏎️" <fynn@fynn.qzz.io>',
      to,
      subject: `Your Pinewood Derby Check-in QR Code 🏁`,
      html: `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
</head>
<body style="margin:0;padding:0;background:#f0f2f8;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f0f2f8;padding:32px 16px;">
  <tr><td align="center">
  <table width="540" cellpadding="0" cellspacing="0" border="0"
         style="max-width:540px;width:100%;background:#ffffff;border-radius:18px;overflow:hidden;box-shadow:0 6px 32px rgba(0,0,0,0.12);">

    <!-- Header -->
    <tr>
      <td style="background:#1a1e2e;padding:32px;text-align:center;">
        <div style="font-size:2.4rem;line-height:1;">🏎️</div>
        <h1 style="margin:10px 0 4px;color:#f59e0b;font-size:1.6rem;font-weight:900;letter-spacing:2px;text-transform:uppercase;">
          Pinewood Derby
        </h1>
        <p style="color:#6b738f;margin:0;font-size:0.8rem;letter-spacing:3px;text-transform:uppercase;">
          You're Registered!
        </p>
      </td>
    </tr>

    <!-- Intro -->
    <tr>
      <td style="padding:32px 32px 0;">
        <h2 style="margin:0 0 10px;color:#1a1e2e;font-size:1.2rem;font-weight:800;">Welcome to the race! 🎉</h2>
        <p style="margin:0 0 24px;color:#4b5563;line-height:1.7;font-size:0.95rem;">
          Your car(s) have been pre-registered. On race day, show the QR code below
          to the Pinewood Derby crew at <strong>check-in</strong> to verify your car(s).
        </p>
      </td>
    </tr>

    <!-- Check-in code badge -->
    <tr>
      <td style="padding:0 32px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td style="background:#f8f9fc;border-radius:14px;padding:24px;text-align:center;border:2px solid #e5e7eb;">
              <div style="font-size:0.65rem;font-weight:700;letter-spacing:5px;text-transform:uppercase;color:#9ca3af;margin-bottom:8px;">
                Your Check-in Code
              </div>
              <div style="font-size:3rem;font-weight:900;color:#1a1e2e;letter-spacing:12px;line-height:1;">
                ${short_code}
              </div>
              <div style="font-size:0.72rem;color:#9ca3af;margin-top:8px;">
                If scanner isn't available, read this code to the crew
              </div>
            </td>
          </tr>
        </table>
      </td>
    </tr>

    <!-- QR code -->
    <tr>
      <td style="padding:24px 32px 8px;">
        <p style="margin:0 0 14px;color:#4b5563;font-size:0.9rem;line-height:1.6;text-align:center;">
          Scan this QR code at the inspection table:
        </p>
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td align="center">
              <img src="${qrUrl}"
                   width="200" height="200"
                   alt="Check-in QR Code"
                   style="display:block;border-radius:12px;border:2px solid #e5e7eb;" />
            </td>
          </tr>
        </table>
      </td>
    </tr>

    <!-- Divider -->
    <tr>
      <td style="padding:28px 32px 0;">
        <div style="border-top:1px solid #e5e7eb;"></div>
      </td>
    </tr>

    <!-- Official Rules -->
    <tr>
      <td style="padding:24px 32px 0;">
        <h3 style="margin:0 0 16px;color:#1a1e2e;font-size:1rem;font-weight:800;text-transform:uppercase;letter-spacing:1px;">
          📋 Official Car Rules
        </h3>
        <p style="margin:0 0 14px;color:#4b5563;font-size:0.9rem;line-height:1.6;">
          Please make sure your car meets <strong>all</strong> of these requirements before race day.
          Cars that don't pass inspection cannot race.
        </p>
        <table width="100%" cellpadding="0" cellspacing="0" border="0">

          <tr>
            <td style="padding:10px 14px;background:#fefce8;border-left:4px solid #f59e0b;border-radius:0 8px 8px 0;">
              <div style="font-weight:700;color:#92400e;font-size:0.88rem;">⚖️ Weight</div>
              <div style="color:#78350f;font-size:0.83rem;margin-top:3px;line-height:1.5;">
                The car must weigh <strong>5 oz (142 g) or less</strong>.
              </div>
            </td>
          </tr>
          <tr><td style="height:8px;"></td></tr>

          <tr>
            <td style="padding:10px 14px;background:#fef2f2;border-left:4px solid #ef4444;border-radius:0 8px 8px 0;">
              <div style="font-weight:700;color:#991b1b;font-size:0.88rem;">📏 Dimensions</div>
              <div style="color:#7f1d1d;font-size:0.83rem;margin-top:3px;line-height:1.5;">
                Max length <strong>7 in</strong> · Max width <strong>2¾ in</strong> · Max height <strong>3 in</strong>.
              </div>
            </td>
          </tr>
          <tr><td style="height:8px;"></td></tr>

          <tr>
            <td style="padding:10px 14px;background:#f0fdf4;border-left:4px solid #22c55e;border-radius:0 8px 8px 0;">
              <div style="font-weight:700;color:#166534;font-size:0.88rem;">🛤️ Track Clearance</div>
              <div style="color:#14532d;font-size:0.83rem;margin-top:3px;line-height:1.5;">
                Nothing may obstruct the top or bottom of the car along the track.
                <strong>No weights on the underside</strong> that would hang below the wheel axles.
              </div>
            </td>
          </tr>
          <tr><td style="height:8px;"></td></tr>

          <tr>
            <td style="padding:10px 14px;background:#eff6ff;border-left:4px solid #3b82f6;border-radius:0 8px 8px 0;">
              <div style="font-weight:700;color:#1e3a8a;font-size:0.88rem;">🔩 Wheels &amp; Axles</div>
              <div style="color:#1e40af;font-size:0.83rem;margin-top:3px;line-height:1.5;">
                Must use the official BSA wheels and axles provided in your kit.
                No bearings, washers, or altered axle grooves.
              </div>
            </td>
          </tr>
          <tr><td style="height:8px;"></td></tr>

          <tr>
            <td style="padding:10px 14px;background:#fdf4ff;border-left:4px solid #a855f7;border-radius:0 8px 8px 0;">
              <div style="font-weight:700;color:#6b21a8;font-size:0.88rem;">🪵 Body</div>
              <div style="color:#581c87;font-size:0.83rem;margin-top:3px;line-height:1.5;">
                Must be made from the official BSA wood block. No metal body parts.
                Decorations must be firmly attached.
              </div>
            </td>
          </tr>
          <tr><td style="height:8px;"></td></tr>

          <tr>
            <td style="padding:10px 14px;background:#fff7ed;border-left:4px solid #f97316;border-radius:0 8px 8px 0;">
              <div style="font-weight:700;color:#9a3412;font-size:0.88rem;">🚫 No Springs / Motors</div>
              <div style="color:#7c2d12;font-size:0.83rem;margin-top:3px;line-height:1.5;">
                The car must be unpowered. No springs, rubber bands, compressed air, or electricity.
              </div>
            </td>
          </tr>

        </table>
      </td>
    </tr>

    <!-- CTA button -->
    <tr>
      <td style="padding:28px 32px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td align="center">
              <a href="${reg_url}"
                 style="display:inline-block;background:#f59e0b;color:#1a1e2e;text-decoration:none;padding:15px 36px;border-radius:10px;font-weight:800;font-size:0.95rem;letter-spacing:0.5px;">
                📱 View Your Registration
              </a>
            </td>
          </tr>
        </table>
      </td>
    </tr>

    <!-- Footer -->
    <tr>
      <td style="background:#f8f9fc;padding:20px 32px;text-align:center;border-top:1px solid #e5e7eb;">
        <p style="margin:0;color:#9ca3af;font-size:0.75rem;line-height:1.6;">
          This email was sent automatically by the Pinewood Derby race management system.<br>
          You will only receive this email once per registered device.
        </p>
      </td>
    </tr>

  </table>
  </td></tr>
</table>
</body>
</html>`,
    });
    console.log("[email] Email sent! messageId:", info.messageId, "| response:", info.response);

    return new Response(JSON.stringify({ ok: true, messageId: info.messageId }), {
      headers: { ...CORS, "Content-Type": "application/json" },
    });
  } catch (err) {
    console.error("[email] ERROR:", String(err));
    console.error("[email] Error name:", (err as Error).name);
    console.error("[email] Error message:", (err as Error).message);
    console.error("[email] Error stack:", (err as Error).stack);
    return new Response(JSON.stringify({ error: String(err), message: (err as Error).message }), {
      status: 500,
      headers: { ...CORS, "Content-Type": "application/json" },
    });
  }
});
