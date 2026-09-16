import { redirect } from "next/navigation";

export default function PipelinesRedirect() {
  redirect("/medical#workflow");
}
