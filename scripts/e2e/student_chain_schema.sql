
CREATE TYPE public.attempt_status AS ENUM (
    'IN_PROGRESS',
    'SUBMITTED',
    'AUTO_SUBMITTED'
);

CREATE TYPE public.audience_type AS ENUM (
    'PROVIDER',
    'BRANCH',
    'BATCH',
    'STUDENT'
);

CREATE TYPE public.batch_status AS ENUM (
    'ACTIVE',
    'INACTIVE'
);

CREATE TYPE public.branch_status AS ENUM (
    'ACTIVE',
    'INACTIVE'
);

CREATE TYPE public.enrollment_status AS ENUM (
    'ACTIVE',
    'INACTIVE'
);

CREATE TYPE public.notification_event_type AS ENUM (
    'TEST_SCHEDULED',
    'TEST_REMINDER',
    'TEST_STARTED',
    'TEST_SUBMITTED',
    'TEST_TIMEOUT',
    'RESULT_PUBLISHED',
    'RANKING_UPDATED',
    'SYSTEM_ANNOUNCEMENT',
    'SYSTEM_MAINTENANCE'
);

CREATE TYPE public.notification_recipient_type AS ENUM (
    'STUDENT',
    'INSTITUTE',
    'ADMIN',
    'SUPER_ADMIN'
);

CREATE TYPE public.provider_status AS ENUM (
    'ACTIVE',
    'INACTIVE'
);

CREATE TYPE public.test_status AS ENUM (
    'DRAFT',
    'PUBLISHED',
    'ACTIVE',
    'INACTIVE',
    'COMPLETED'
);

CREATE FUNCTION public.result_history_immutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN RAISE EXCEPTION 'result_history is append-only'; END;
            $$;

CREATE TABLE public.attempt_answers (
    answer_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    question_id uuid NOT NULL,
    selected_answer character varying(10),
    answered_at timestamp without time zone,
    updated_at timestamp without time zone NOT NULL,
    question_state character varying(30) NOT NULL,
    visited_at timestamp with time zone
);

CREATE TABLE public.batches (
    batch_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    start_date date,
    end_date date,
    status public.batch_status NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    branch_id uuid NOT NULL
);

CREATE TABLE public.branches (
    branch_id uuid NOT NULL,
    provider_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    address text,
    city character varying(100),
    state character varying(100),
    pincode character varying(10),
    status public.branch_status NOT NULL,
    created_ip_address inet,
    created_latitude numeric(10,7),
    created_longitude numeric(10,7),
    updated_ip_address inet,
    updated_latitude numeric(10,7),
    updated_longitude numeric(10,7),
    deleted_at timestamp without time zone,
    deleted_ip_address inet,
    deleted_latitude numeric(10,7),
    deleted_longitude numeric(10,7),
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);

CREATE TABLE public.enrollment_batches (
    enrollment_batch_id uuid NOT NULL,
    enrollment_id character varying(36) NOT NULL,
    batch_id uuid NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);

CREATE TABLE public.enrollments (
    enrollment_id character varying(36) NOT NULL,
    provider_id uuid NOT NULL,
    student_id character varying(20) NOT NULL,
    enrolled_at timestamp without time zone NOT NULL,
    status public.enrollment_status NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    branch_id uuid NOT NULL
);

CREATE TABLE public.m3_alembic_version (
    version_num character varying(32) NOT NULL
);

CREATE TABLE public.m4_alembic_version (
    version_num character varying(32) NOT NULL
);

CREATE TABLE public.m5_alembic_version (
    version_num character varying(32) NOT NULL
);

CREATE TABLE public.m6_alembic_version (
    version_num character varying(32) NOT NULL
);

CREATE TABLE public.m9_alembic_version (
    version_num character varying(32) NOT NULL
);

CREATE TABLE public.notification_recipients (
    notification_recipient_id uuid NOT NULL,
    notification_id uuid NOT NULL,
    recipient_type public.notification_recipient_type NOT NULL,
    recipient_id character varying(50) NOT NULL,
    is_read boolean NOT NULL,
    read_at timestamp with time zone
);

CREATE TABLE public.notifications (
    notification_id uuid NOT NULL,
    event_type public.notification_event_type NOT NULL,
    title character varying(255) NOT NULL,
    message text NOT NULL,
    metadata jsonb,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.providers (
    provider_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    email character varying(255) NOT NULL,
    phone character varying(20),
    address text,
    status public.provider_status NOT NULL,
    created_ip_address inet,
    created_latitude numeric(10,7),
    created_longitude numeric(10,7),
    updated_ip_address inet,
    updated_latitude numeric(10,7),
    updated_longitude numeric(10,7),
    deleted_at timestamp without time zone,
    deleted_ip_address inet,
    deleted_latitude numeric(10,7),
    deleted_longitude numeric(10,7),
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);

CREATE TABLE public.questions (
    question_id uuid NOT NULL,
    test_id uuid NOT NULL,
    question_order integer NOT NULL,
    question_text text NOT NULL,
    question_type character varying(50) NOT NULL,
    option_a text,
    option_b text,
    option_c text,
    option_d text,
    correct_answer character varying(10) NOT NULL,
    marks integer NOT NULL,
    negative_marks integer NOT NULL,
    image_reference text,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL,
    subject character varying(100)
);

CREATE TABLE public.result_history (
    history_id uuid NOT NULL,
    result_id uuid NOT NULL,
    test_id uuid NOT NULL,
    student_id character varying(20) NOT NULL,
    kind character varying(20) NOT NULL,
    correction_ref character varying(100) NOT NULL,
    previous jsonb NOT NULL,
    new jsonb NOT NULL,
    reason text NOT NULL,
    actor character varying(100) NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT result_history_kind_check CHECK (((kind)::text = ANY ((ARRAY['MARKS_ADJUSTMENT'::character varying, 'REEVALUATION'::character varying])::text[])))
);

CREATE TABLE public.students (
    student_id character varying(20) NOT NULL,
    first_name character varying(255) DEFAULT ''::character varying NOT NULL,
    last_name character varying(255) DEFAULT ''::character varying NOT NULL,
    email character varying(255) DEFAULT (gen_random_uuid())::text NOT NULL,
    phone character varying(20),
    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL,
    deleted_at timestamp without time zone
);

CREATE TABLE public.test_attempts (
    attempt_id uuid NOT NULL,
    test_id uuid NOT NULL,
    student_id character varying NOT NULL,
    started_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    submitted_at timestamp with time zone,
    status public.attempt_status NOT NULL
);

CREATE TABLE public.test_audiences (
    test_audience_id uuid NOT NULL,
    test_id uuid NOT NULL,
    audience_type public.audience_type NOT NULL,
    provider_id uuid,
    branch_id uuid,
    batch_id uuid,
    student_id character varying(20),
    created_at timestamp without time zone NOT NULL
);

CREATE TABLE public.test_results (
    result_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    test_id uuid NOT NULL,
    student_id character varying(20) NOT NULL,
    total_questions integer NOT NULL,
    attempted_questions integer NOT NULL,
    correct_answers integer NOT NULL,
    incorrect_answers integer NOT NULL,
    unanswered_questions integer NOT NULL,
    total_marks integer NOT NULL,
    obtained_marks integer NOT NULL,
    accuracy double precision NOT NULL,
    created_at timestamp with time zone NOT NULL
);

CREATE TABLE public.tests (
    test_id uuid NOT NULL,
    provider_id uuid NOT NULL,
    branch_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    duration_minutes integer NOT NULL,
    total_questions integer NOT NULL,
    total_marks integer NOT NULL,
    start_at timestamp without time zone,
    end_at timestamp without time zone,
    status public.test_status NOT NULL,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);

ALTER TABLE ONLY public.attempt_answers
    ADD CONSTRAINT attempt_answers_pkey PRIMARY KEY (answer_id);

ALTER TABLE ONLY public.batches
    ADD CONSTRAINT batches_pkey PRIMARY KEY (batch_id);

ALTER TABLE ONLY public.branches
    ADD CONSTRAINT branches_pkey PRIMARY KEY (branch_id);

ALTER TABLE ONLY public.enrollment_batches
    ADD CONSTRAINT enrollment_batches_pkey PRIMARY KEY (enrollment_batch_id);

ALTER TABLE ONLY public.enrollments
    ADD CONSTRAINT enrollments_pkey PRIMARY KEY (enrollment_id);

ALTER TABLE ONLY public.m3_alembic_version
    ADD CONSTRAINT m3_alembic_version_pkc PRIMARY KEY (version_num);

ALTER TABLE ONLY public.m4_alembic_version
    ADD CONSTRAINT m4_alembic_version_pkc PRIMARY KEY (version_num);

ALTER TABLE ONLY public.m5_alembic_version
    ADD CONSTRAINT m5_alembic_version_pkc PRIMARY KEY (version_num);

ALTER TABLE ONLY public.m6_alembic_version
    ADD CONSTRAINT m6_alembic_version_pkc PRIMARY KEY (version_num);

ALTER TABLE ONLY public.m9_alembic_version
    ADD CONSTRAINT m9_alembic_version_pkc PRIMARY KEY (version_num);

ALTER TABLE ONLY public.notification_recipients
    ADD CONSTRAINT notification_recipients_pkey PRIMARY KEY (notification_recipient_id);

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (notification_id);

ALTER TABLE ONLY public.providers
    ADD CONSTRAINT providers_email_key UNIQUE (email);

ALTER TABLE ONLY public.providers
    ADD CONSTRAINT providers_pkey PRIMARY KEY (provider_id);

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT questions_pkey PRIMARY KEY (question_id);

ALTER TABLE ONLY public.result_history
    ADD CONSTRAINT result_history_pkey PRIMARY KEY (history_id);

ALTER TABLE ONLY public.students
    ADD CONSTRAINT students_email_key UNIQUE (email);

ALTER TABLE ONLY public.students
    ADD CONSTRAINT students_pkey PRIMARY KEY (student_id);

ALTER TABLE ONLY public.test_attempts
    ADD CONSTRAINT test_attempts_pkey PRIMARY KEY (attempt_id);

ALTER TABLE ONLY public.test_audiences
    ADD CONSTRAINT test_audiences_pkey PRIMARY KEY (test_audience_id);

ALTER TABLE ONLY public.test_results
    ADD CONSTRAINT test_results_pkey PRIMARY KEY (result_id);

ALTER TABLE ONLY public.tests
    ADD CONSTRAINT tests_pkey PRIMARY KEY (test_id);

ALTER TABLE ONLY public.attempt_answers
    ADD CONSTRAINT uq_attempt_answers_attempt_question UNIQUE (attempt_id, question_id);

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT uq_questions_test_order UNIQUE (test_id, question_order);

ALTER TABLE ONLY public.result_history
    ADD CONSTRAINT uq_result_history_ref_result UNIQUE (correction_ref, result_id);

ALTER TABLE ONLY public.test_attempts
    ADD CONSTRAINT uq_test_attempts_student_test UNIQUE (student_id, test_id);

ALTER TABLE ONLY public.test_results
    ADD CONSTRAINT uq_test_results_attempt UNIQUE (attempt_id);

CREATE INDEX ix_notification_recipients_notification_id ON public.notification_recipients USING btree (notification_id);

CREATE INDEX ix_notification_recipients_recipient_id ON public.notification_recipients USING btree (recipient_id);

CREATE INDEX ix_notification_recipients_recipient_type ON public.notification_recipients USING btree (recipient_type);

CREATE INDEX ix_notifications_event_type ON public.notifications USING btree (event_type);

CREATE INDEX ix_questions_test_id ON public.questions USING btree (test_id);

CREATE INDEX ix_questions_test_subject ON public.questions USING btree (test_id, subject);

CREATE INDEX ix_result_history_result_id ON public.result_history USING btree (result_id);

CREATE INDEX ix_result_history_test_id ON public.result_history USING btree (test_id);

CREATE TRIGGER result_history_immutable BEFORE DELETE OR UPDATE ON public.result_history FOR EACH ROW EXECUTE FUNCTION public.result_history_immutable();

ALTER TABLE ONLY public.attempt_answers
    ADD CONSTRAINT attempt_answers_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.test_attempts(attempt_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.enrollment_batches
    ADD CONSTRAINT enrollment_batches_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.batches(batch_id);

ALTER TABLE ONLY public.enrollment_batches
    ADD CONSTRAINT enrollment_batches_enrollment_id_fkey FOREIGN KEY (enrollment_id) REFERENCES public.enrollments(enrollment_id);

ALTER TABLE ONLY public.enrollments
    ADD CONSTRAINT enrollments_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES public.providers(provider_id);

ALTER TABLE ONLY public.enrollments
    ADD CONSTRAINT enrollments_student_id_fkey FOREIGN KEY (student_id) REFERENCES public.students(student_id);

ALTER TABLE ONLY public.batches
    ADD CONSTRAINT fk_batches_branch_id FOREIGN KEY (branch_id) REFERENCES public.branches(branch_id);

ALTER TABLE ONLY public.branches
    ADD CONSTRAINT fk_branches_provider_id FOREIGN KEY (provider_id) REFERENCES public.providers(provider_id);

ALTER TABLE ONLY public.enrollments
    ADD CONSTRAINT fk_enrollments_branch_id FOREIGN KEY (branch_id) REFERENCES public.branches(branch_id);

ALTER TABLE ONLY public.questions
    ADD CONSTRAINT fk_questions_test_id FOREIGN KEY (test_id) REFERENCES public.tests(test_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.tests
    ADD CONSTRAINT fk_tests_branch_id FOREIGN KEY (branch_id) REFERENCES public.branches(branch_id);

ALTER TABLE ONLY public.tests
    ADD CONSTRAINT fk_tests_provider_id FOREIGN KEY (provider_id) REFERENCES public.providers(provider_id);

ALTER TABLE ONLY public.notification_recipients
    ADD CONSTRAINT notification_recipients_notification_id_fkey FOREIGN KEY (notification_id) REFERENCES public.notifications(notification_id) ON DELETE CASCADE;

ALTER TABLE ONLY public.result_history
    ADD CONSTRAINT result_history_result_id_fkey FOREIGN KEY (result_id) REFERENCES public.test_results(result_id);

