type Props = {
  title: string;
  body: string;
};

export default function PublicErrorState({ title, body }: Props) {
  return (
    <section className="notice panel" role="alert">
      <h2 className="notice-title">{title}</h2>
      <p>{body}</p>
    </section>
  );
}
