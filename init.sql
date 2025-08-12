
CREATE TABLE public.users (
	chat_id int8 NOT NULL,
	CONSTRAINT users_pkey PRIMARY KEY (chat_id)
);

CREATE TABLE public.routes (
	route_id serial4 NOT NULL,
	city_from text NULL,
	city_to text NULL,
	"date" text NULL,
	url text NULL,
	CONSTRAINT routes_pkey PRIMARY KEY (route_id),
	CONSTRAINT routes_url_key UNIQUE (url)
);

CREATE TABLE public.trains (
	train_id serial4 NOT NULL,
	route_id int4 NOT NULL,
	train_number text NULL,
	time_depart text NULL,
	time_arriv text NULL,
	CONSTRAINT trains_pkey PRIMARY KEY (train_id),
	CONSTRAINT trains_route_id_train_number_time_depart_time_arriv_key UNIQUE (route_id, train_number, time_depart, time_arriv),
	CONSTRAINT trains_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.routes(route_id)
);

CREATE TABLE public.tracking (
	tracking_id serial4 NOT NULL,
	chat_id int8 NOT NULL,
	train_id int4 NOT NULL,
	json_ticket_dict text NULL,
	CONSTRAINT tracking_chat_id_train_id_key UNIQUE (chat_id, train_id),
	CONSTRAINT tracking_pkey PRIMARY KEY (tracking_id),
	CONSTRAINT tracking_chat_id_fkey FOREIGN KEY (chat_id) REFERENCES public.users(chat_id),
	CONSTRAINT tracking_train_id_fkey FOREIGN KEY (train_id) REFERENCES public.trains(train_id)
);
