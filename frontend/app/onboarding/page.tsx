import { redirect } from "next/navigation";

export default async function OnboardingPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const { status } = await searchParams;
  redirect(status === "healthcare_pending" ? "/?onboarding=healthcare_pending" : "/?onboarding=select");
}
