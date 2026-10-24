import { Card, CardContent } from "@/components/ui/card";

export function ComingSoon({ title, milestone }: { title: string; milestone: "B" | "C" | "D" }) {
  return (
    <Card>
      <CardContent className="py-10 text-center text-sm text-muted-foreground">
        <p className="font-medium text-foreground">{title}</p>
        <p className="mt-1">Coming in milestone {milestone}.</p>
      </CardContent>
    </Card>
  );
}
